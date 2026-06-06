import os
import re
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime

from app.services.jellyseerr import (
    jelly_fetch_issue, jelly_last_human_comment, jelly_comment, jelly_close, is_our_comment
)
from app.services import radarr as R
from app.services import sonarr as S
from app.services import bazarr as B
from app.services.notify import notify
from app.config import cfg

log = logging.getLogger("remediarr")

# env/config
PREFIX = os.getenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]")
CLOSE_ISSUES = os.getenv("JELLYSEERR_CLOSE_ISSUES", "true").lower() == "true"
COMMENT_ON_ACTION = os.getenv("JELLYSEERR_COMMENT_ON_ACTION", "true").lower() == "true"

# Messages - Your requested format
MSG_MOVIE_SUCCESS = os.getenv("MSG_MOVIE_SUCCESS", "{title}: replaced file; new download grabbed. Closing this issue. If anything's still off, comment and I'll take another pass.")
MSG_TV_SUCCESS = os.getenv("MSG_TV_SUCCESS", "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue. If anything's still off, comment and I'll take another pass.")
# CONFIRM_REPLACEMENT_IMPORT messages (only used when that flag is on).
MSG_TV_SEARCHING = os.getenv("MSG_TV_SEARCHING", "{title} S{season:02d}E{episode:02d}: deleted the file and started a re-download. I'll comment here once the replacement has imported.")
MSG_TV_IMPORTED = os.getenv("MSG_TV_IMPORTED", "{title} S{season:02d}E{episode:02d}: replacement downloaded and imported. Closing this issue. If anything's still off, comment and I'll take another pass.")

# Keyword buckets (lower-cased sets)
def _csv(name: str) -> List[str]:
    return [s.strip().lower() for s in os.getenv(name, "").split(",") if s.strip()]

TV_AUDIO = set(_csv("TV_AUDIO_KEYWORDS") or ["no audio", "no sound", "missing audio", "audio issue"])
TV_VIDEO = set(_csv("TV_VIDEO_KEYWORDS") or ["no video", "video glitch", "black screen", "stutter", "pixelation"])
TV_SUBS = set(_csv("TV_SUBTITLE_KEYWORDS") or ["missing subs", "no subtitles", "bad subtitles", "wrong subs", "subs out of sync"])
TV_OTHER = set(_csv("TV_OTHER_KEYWORDS") or ["buffering", "playback error", "corrupt file"])
MOV_AUDIO = set(_csv("MOVIE_AUDIO_KEYWORDS") or ["no audio", "no sound", "audio issue"])
MOV_VIDEO = set(_csv("MOVIE_VIDEO_KEYWORDS") or ["no video", "video missing", "bad video", "broken video", "black screen"])
MOV_SUBS = set(_csv("MOVIE_SUBTITLE_KEYWORDS") or ["missing subs", "no subtitles", "bad subtitles", "wrong subs", "subs out of sync"])
MOV_OTHER = set(_csv("MOVIE_OTHER_KEYWORDS") or ["buffering", "playback error", "corrupt file"])
MOV_WRONG = set(_csv("MOVIE_WRONG_KEYWORDS") or ["not the right movie", "wrong movie", "incorrect movie"])

def _bucket_for(text: str, media_type: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    
    # Check for wrong movie first (movie-specific)
    if media_type == "movie":
        for keyword in MOV_WRONG:
            if keyword in t:
                return "wrong"
    
    # Check other buckets using substring matching
    for keyword in (MOV_AUDIO | TV_AUDIO):
        if keyword in t:
            return "audio"
    
    for keyword in (MOV_VIDEO | TV_VIDEO):
        if keyword in t:
            return "video"
    
    for keyword in (MOV_SUBS | TV_SUBS):
        if keyword in t:
            return "subtitle"
    
    for keyword in (MOV_OTHER | TV_OTHER):
        if keyword in t:
            return "other"

    return None

# Seerr issue TYPE -> bucket, for ISSUE_TYPE_AS_BUCKET mode (no comment needed).
# Ints match the upstream Overseerr/Jellyseerr IssueType enum
# (server/constants/issue.ts: VIDEO=1, AUDIO=2, SUBTITLES=3, OTHER=4). The webhook
# body's string `issue_type` is preferred; this int `issueType` is the fallback.
_ISSUE_TYPE_INT_TO_BUCKET = {1: "video", 2: "audio", 3: "subtitle", 4: "other"}
_VALID_TYPE_BUCKETS = {"audio", "video", "subtitle", "other"}

def _bucket_from_issue_type(issue: Dict[str, Any]) -> Optional[str]:
    """Derive the bucket straight from the Seerr issue TYPE (comment ignored).

    Prefers the webhook-body string ``issue_type`` ("audio"/"video"/...);
    falls back to the API integer ``issueType`` (1=video, 2=audio, 3=subtitle,
    4=other). Returns None if neither yields a valid bucket. "wrong" is not
    reachable here (Seerr has no "wrong" issue type) — that stays keyword-only.
    """
    if not issue:
        return None
    raw = str(issue.get("issue_type") or "").strip().lower()
    if raw in _VALID_TYPE_BUCKETS:
        return raw
    iv = _to_int_or_none(issue.get("issueType"))
    if iv in _ISSUE_TYPE_INT_TO_BUCKET:
        return _ISSUE_TYPE_INT_TO_BUCKET[iv]
    return None

def _to_int_or_none(val) -> Optional[int]:
    try:
        if isinstance(val, bool):
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            s = val.strip()
            # Skip template placeholders
            if s.startswith("{{") and s.endswith("}}"):
                return None
            # Skip empty or placeholder values
            if s in ("", "null", "undefined", "None"):
                return None
            # Extract first number found
            m = re.search(r"\d+", s)
            if m:
                num = int(m.group())
                # Sanity check - seasons shouldn't be > 100, episodes shouldn't be > 1000
                if num > 1000:
                    return None
                return num
            return None
    except Exception:
        return None
    return None

def _key_looks_like(name: str, want: str) -> bool:
    n = name.lower()
    if want == "season":
        return ("season" in n) and ("reason" not in n)
    if want == "episode":
        return "episode" in n
    return False

def _maybe_int_from_obj(v: Any) -> Optional[int]:
    if isinstance(v, dict):
        for _, v2 in v.items():
            iv = _to_int_or_none(v2)
            if iv is not None:
                return iv
    return _to_int_or_none(v)

def _walk_for_season_episode(o: Any) -> Tuple[Optional[int], Optional[int]]:
    s_found: Optional[int] = None
    e_found: Optional[int] = None
    def _walk(node: Any):
        nonlocal s_found, e_found
        if node is None or (s_found is not None and e_found is not None):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                # Be more specific about what keys we accept
                if s_found is None and _key_looks_like(k, "season"):
                    sv = _maybe_int_from_obj(v)
                    # Sanity check: seasons should be 1-50
                    if sv is not None and 1 <= sv <= 50:
                        s_found = sv
                if e_found is None and _key_looks_like(k, "episode"):
                    ev = _maybe_int_from_obj(v)
                    # Sanity check: episodes should be 1-999, not series IDs (which are often 100+)
                    if ev is not None and 1 <= ev <= 999:
                        # Additional check: if this looks like a series ID (>100), skip it
                        if ev > 100 and k.lower() in ["id", "seriesid", "series_id"]:
                            continue
                        e_found = ev
                _walk(v)
        elif isinstance(node, list):
            for it in node:
                _walk(it)
    _walk(o)
    return s_found, e_found

def _extract_season_episode_from_text(text: str) -> Tuple[Optional[int], Optional[int]]:
    m = re.search(r"[Ss](\d{1,3})[Ee](\d{1,3})", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    sm = re.search(r"Season\s+(\d{1,3})", text or "", re.IGNORECASE)
    em = re.search(r"Episode\s+(\d{1,3})", text or "", re.IGNORECASE)
    return (int(sm.group(1)) if sm else None, int(em.group(1)) if em else None)

# In-memory cooldown to avoid loops
_COOLDOWN: Dict[int, float] = {}
_COOLDOWN_SEC = int(os.getenv("REMEDIARR_ISSUE_COOLDOWN_SEC", "90"))

def _under_cooldown(issue_id: int) -> bool:
    import time
    now = time.time()
    ts = _COOLDOWN.get(issue_id, 0)
    if now < ts:
        rem = int(ts - now)
        log.info("Issue %s under cooldown (%ss remaining) — skipping.", issue_id, rem)
        return True
    return False

def _bump_cooldown(issue_id: int) -> None:
    import time
    _COOLDOWN[issue_id] = time.time() + _COOLDOWN_SEC

_ALL_EPISODES_SENTINELS = {"all episodes", "all", "0", ""}

def _parse_episode_list_from_text(text: str, known_season: Optional[int] = None) -> List[int]:
    """Extract specific episode numbers from free text.
    Handles: 'episodes 3,4,5,6,22', 'ep 3-6 and 22', 'eps 3, 4, 5'.
    Returns sorted deduplicated list, or [] if nothing specific found.
    """
    if not text:
        return []
    episodes: set = set()
    # Match after episode/ep/eps keyword
    for m in re.finditer(r'\bep(?:isodes?|s)?\s+([\d,\s\-\–]+(?:and\s+[\d,\s\-\–]+)*)', text, re.IGNORECASE):
        block = m.group(1)
        # Expand ranges like 3-6
        for rng in re.finditer(r'(\d+)\s*[-\–]\s*(\d+)', block):
            start, end = int(rng.group(1)), int(rng.group(2))
            if start < end and (end - start) <= 50:
                episodes.update(range(start, end + 1))
        # Individual numbers
        for n in re.finditer(r'\d+', block):
            val = int(n.group())
            if 1 <= val <= 999:
                episodes.add(val)
    if known_season is not None:
        episodes.discard(known_season)
    return sorted(episodes)


def _is_all_episodes(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float)) and int(value) == 0:
        return True
    if isinstance(value, str) and value.strip().lower() in _ALL_EPISODES_SENTINELS:
        return True
    return False

# CONFIRM_REPLACEMENT_IMPORT: issues awaiting a Sonarr "On Import" webhook before we
# comment success + close. Keyed by (series_id, season, episode) -> {"issue_ids": set,
# "title": str}. One episode can have several open issues (a re-open, or two people
# reporting the same break); all of them close on the single import. In-memory and
# best-effort: cleared on restart (a pending issue simply stays open — fail-safe).
_PENDING_IMPORTS: Dict[Tuple[int, int, int], Dict[str, Any]] = {}

def _register_pending_import(series_id: int, season: int, episode: int,
                             issue_id: int, title: str) -> bool:
    """Arm an issue to be closed when Sonarr imports (series_id, season, episode).

    Returns True if this is the first issue waiting on that episode (the caller
    should delete + re-search), or False if a remediation for the same episode is
    already in flight (the caller should NOT re-delete/re-search the in-progress
    download — just merge this issue_id; all waiting issues close on the import).
    """
    key = (series_id, season, episode)
    existing = _PENDING_IMPORTS.get(key)
    if existing is None:
        _PENDING_IMPORTS[key] = {"issue_ids": {issue_id}, "title": title}
        fresh = True
    else:
        existing["issue_ids"].add(issue_id)
        fresh = False
    log.info("Awaiting Sonarr import to confirm issue %s (%s S%02dE%02d) — %s; %d episode(s) pending",
             issue_id, title, season, episode,
             "new remediation" if fresh else "merged into in-flight remediation",
             len(_PENDING_IMPORTS))
    return fresh

def _sonarr_import_keys(payload: Dict[str, Any]) -> List[Tuple[int, int, int]]:
    """Extract (series_id, season, episode) keys from a Sonarr Connect webhook.

    Any "Download" import yields keys — a fresh import OR an upgrade. A new file
    landing for a pending episode is a plausible fix regardless of why; and since
    our remediation deletes first, the normal replacement lands as a fresh import,
    so an upgrade event only ever matches a pending entry whose episode already
    has a file — i.e. it usefully backstops a missed fresh-import webhook. Other
    events (Test, Grab, Rename, health, etc.) yield none so the endpoint no-ops.
    """
    if (payload.get("eventType") or "") != "Download":
        return []
    series = payload.get("series") or {}
    sid = series.get("id")
    if not isinstance(sid, int):
        return []
    keys: List[Tuple[int, int, int]] = []
    for ep in (payload.get("episodes") or []):
        s = ep.get("seasonNumber")
        e = ep.get("episodeNumber")
        if isinstance(s, int) and isinstance(e, int):
            keys.append((sid, s, e))
    return keys

async def _tv_episode_from_payload(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], int, int]:
    """Returns (series_id, series, season, episode) where episode=0 means all episodes in season."""
    issue = payload.get("issue") or {}
    media = payload.get("media") or {}
    comment = payload.get("comment") or {}

    tvdb_id = media.get("tvdbId") or media.get("tvdbid")
    if not tvdb_id:
        raise ValueError("Missing tvdbId")

    # Try explicit keys first - handle both string and int values
    season_candidates = [
        issue.get("problemSeason"),  # This is the correct field name!
        issue.get("affected_season"),
        issue.get("affectedSeason"),
        media.get("seasonNumber"),
        media.get("season")
    ]
    episode_candidates = [
        issue.get("problemEpisode"),  # This is the correct field name!
        issue.get("affected_episode"),
        issue.get("affectedEpisode"),
        media.get("episodeNumber"),
        media.get("episode")
    ]

    season = None
    episode = None
    all_episodes_in_season = False

    # Check if episode field explicitly signals "all episodes"
    raw_episode = issue.get("problemEpisode") or issue.get("affected_episode") or issue.get("affectedEpisode")
    if _is_all_episodes(raw_episode):
        all_episodes_in_season = True

    # Extract season
    for candidate in season_candidates:
        if candidate is not None:
            # Handle string values that might be numbers
            if isinstance(candidate, str):
                candidate = candidate.strip()
                if candidate and not candidate.startswith("{{"):
                    try:
                        season = int(candidate)
                        break
                    except ValueError:
                        continue
            elif isinstance(candidate, (int, float)):
                season = int(candidate)
                break

    # Extract episode (skip if already flagged as all-episodes)
    if not all_episodes_in_season:
        for candidate in episode_candidates:
            if candidate is not None:
                if isinstance(candidate, str):
                    candidate = candidate.strip()
                    if candidate and not candidate.startswith("{{"):
                        try:
                            episode = int(candidate)
                            break
                        except ValueError:
                            continue
                elif isinstance(candidate, (int, float)):
                    episode = int(candidate)
                    break

    log.info("After explicit extraction: season=%s, episode=%s, all_episodes=%s", season, episode, all_episodes_in_season)

    if not all_episodes_in_season:
        # If we got both from explicit extraction, skip fallbacks
        if season is not None and episode is not None:
            log.info("Found season/episode from explicit extraction: S%02dE%02d", season, episode)
        else:
            # Fallback: parse from text
            if season is None or episode is None:
                text = " ".join([
                    str(payload.get("subject") or ""),
                    str(issue.get("issue_type") or ""),
                    str(issue.get("issue_status") or ""),
                    str(payload.get("message") or ""),
                    str(comment.get("comment_message") or ""),
                    str(comment.get("message") or "")
                ])
                s2, e2 = _extract_season_episode_from_text(text)
                season = season if season is not None else s2
                episode = episode if episode is not None else e2
                if s2 or e2:
                    log.info("After text parsing: season=%s, episode=%s", season, episode)

            # Fallback: walk the entire payload for season/episode
            if season is None or episode is None:
                s3, e3 = _walk_for_season_episode(payload)
                season = season if season is not None else s3
                episode = episode if episode is not None else e3
                if s3 or e3:
                    log.info("After payload walk: season=%s, episode=%s", season, episode)

    # Get series from Sonarr
    series = await S.get_series_by_tvdb(int(tvdb_id))
    if not series:
        raise ValueError("Series not found in Sonarr")

    if season is None:
        raise ValueError("Missing season number after all extraction attempts")

    # Sanity check season
    if season < 1 or season > 50:
        raise ValueError(f"Invalid season number: {season}")

    # All-episodes path: return episode=0 as sentinel
    if all_episodes_in_season or episode is None:
        log.info("All-episodes mode for series %s season %s", series["id"], season)
        return series["id"], series, int(season), 0

    if episode < 1 or episode > 1000:
        raise ValueError(f"Invalid episode number: {episode}")

    return series["id"], series, int(season), int(episode)

def _is_bazarr_enabled() -> bool:
    """Check if Bazarr is configured and enabled."""
    return bool(cfg.BAZARR_URL and cfg.BAZARR_API_KEY)

async def _handle_subtitle_with_bazarr(issue_id: int, media_type: str, media_id: int, title: str, 
                                      season: Optional[int] = None, episode: Optional[int] = None) -> bool:
    """Handle subtitle issues using Bazarr. Returns True if handled successfully."""
    if not _is_bazarr_enabled():
        return False
    
    try:
        if media_type == "movie":
            # Get Radarr movie ID and find corresponding Bazarr movie
            bazarr_movie = await B.get_movie_by_radarr_id(media_id)
            if not bazarr_movie:
                log.warning("Movie %s not found in Bazarr", media_id)
                return False
            
            bazarr_id = bazarr_movie.get("id")
            if not bazarr_id:
                log.warning("Bazarr movie missing ID: %s", bazarr_movie)
                return False
            
            # Delete existing subtitles and trigger new search
            deleted = await B.delete_movie_subtitles(bazarr_id)
            log.info("Deleted %s subtitle files for movie %s", deleted, bazarr_id)
            
            success = await B.search_movie_subtitles(bazarr_id)
            if success:
                msg = f"{title}: searched for new subtitles using Bazarr"
                if COMMENT_ON_ACTION:
                    await jelly_comment(issue_id, f"{PREFIX} {msg}")
                await notify(f"Remediarr - Movie Subtitles", msg)
                return True
                
        elif media_type == "tv":
            # Get Sonarr series ID and find corresponding Bazarr series
            bazarr_series = await B.get_series_by_tvdb(media_id)  # Note: this needs TVDB ID, not Sonarr ID
            if not bazarr_series:
                log.warning("Series %s not found in Bazarr", media_id)
                return False
            
            # For TV, we need to find the specific episode in Bazarr
            # This is more complex as we need to match season/episode
            # For now, trigger a general subtitle search for the series
            success = await B.trigger_wanted_search("series")
            if success:
                msg = f"{title} S{season:02d}E{episode:02d}: searched for new subtitles using Bazarr"
                if COMMENT_ON_ACTION:
                    await jelly_comment(issue_id, f"{PREFIX} {msg}")
                await notify(f"Remediarr - TV Subtitles", msg)
                return True
                
    except Exception as e:
        log.error("Bazarr subtitle handling failed: %s", e)
        return False
    
    return False

async def _handle_movie(issue_id: int, movie: Dict[str, Any], bucket: str) -> None:
    movie_id = movie["id"]
    title = movie.get("title") or f"Movie {movie_id}"
    
    # Handle subtitle issues with Bazarr if enabled and configured
    if bucket == "subtitle":
        bazarr_handled = await _handle_subtitle_with_bazarr(issue_id, "movie", movie_id, title)
        if bazarr_handled:
            # Close issue if Bazarr handled it successfully
            if CLOSE_ISSUES:
                closed = await jelly_close(issue_id)
                log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
            return
        
        # Fall back to traditional approach if Bazarr not available or failed
        log.info("Falling back to traditional subtitle handling for movie %s", movie_id)
    
    # Delete files if needed (traditional approach)
    removed = 0
    if bucket in ("audio", "video", "subtitle", "wrong"):
        log.info("Deleting movie files for movie %s", movie_id)
        removed = await R.delete_moviefiles(movie_id)
        log.info("Deleted %s movie files", removed)

    # Trigger search
    log.info("Triggering search for movie %s", movie_id)
    await R.trigger_search_movie(movie_id)
    
    # Comment and close
    msg = MSG_MOVIE_SUCCESS.format(title=title)
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {msg}")
    if CLOSE_ISSUES:
        closed = await jelly_close(issue_id)
        log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
    
    await notify(f"Remediarr - Movie", f"{title}: fixed")

async def _handle_tv_specific_episodes(issue_id: int, series: Dict[str, Any], season: int, episodes: List[int], bucket: str) -> None:
    series_id = series["id"]
    title = series.get("title") or f"Series {series_id}"

    all_episode_ids: List[int] = []
    handled_eps: List[int] = []

    for ep_num in episodes:
        ep_ids = await S.episode_ids_for(series_id, season, ep_num)
        if not ep_ids:
            log.info("No episode file in Sonarr for S%02dE%02d, skipping", season, ep_num)
            continue
        if bucket in ("audio", "video", "subtitle"):
            removed = await S.delete_episodefiles(series_id, ep_ids)
            log.info("Deleted %s files for S%02dE%02d", removed, season, ep_num)
        all_episode_ids.extend(ep_ids)
        handled_eps.append(ep_num)

    if not handled_eps:
        log.info("No matching episode files found in Sonarr for S%02d eps %s", season, episodes)
        return

    await S.trigger_episode_search(all_episode_ids)

    ep_list = ", ".join(f"E{e:02d}" for e in handled_eps)
    msg = f"{title} S{season:02d} ({ep_list}): replaced files; new downloads grabbed. Closing this issue. If anything's still off, comment and I'll take another pass."
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {msg}")
    if CLOSE_ISSUES:
        closed = await jelly_close(issue_id)
        log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
    await notify(f"Remediarr - TV", f"{title} S{season:02d} {ep_list}: fixed")


async def _handle_tv_season(issue_id: int, series: Dict[str, Any], season: int, bucket: str) -> None:
    series_id = series["id"]
    title = series.get("title") or f"Series {series_id}"

    if bucket == "subtitle":
        tvdb_id = series.get("tvdbId")
        if tvdb_id and _is_bazarr_enabled():
            bazarr_handled = await _handle_subtitle_with_bazarr(issue_id, "tv", tvdb_id, title, season, None)
            if bazarr_handled:
                if CLOSE_ISSUES:
                    closed = await jelly_close(issue_id)
                    log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
                return
        log.info("Falling back to traditional subtitle handling for series %s season %s", series_id, season)

    if bucket in ("audio", "video", "subtitle"):
        removed = await S.delete_all_episodefiles_for_season(series_id, season)
        log.info("Deleted %s episode files for series %s season %s", removed, series_id, season)

    await S.trigger_season_search(series_id, season)
    log.info("Triggered SeasonSearch for series %s season %s", series_id, season)

    msg = f"{title} Season {season:02d}: replaced files; new downloads grabbed. Closing this issue. If anything's still off, comment and I'll take another pass."
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {msg}")
    if CLOSE_ISSUES:
        closed = await jelly_close(issue_id)
        log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")

    await notify(f"Remediarr - TV Season", f"{title} Season {season}: fixed")


async def _handle_tv(issue_id: int, series: Dict[str, Any], season: int, episode: int, episode_ids: List[int], bucket: str) -> None:
    series_id = series["id"]
    title = series.get("title") or f"Series {series_id}"
    
    # Handle subtitle issues with Bazarr if enabled and configured
    if bucket == "subtitle":
        # For TV shows, we need the TVDB ID to find the series in Bazarr
        tvdb_id = series.get("tvdbId")
        if tvdb_id:
            bazarr_handled = await _handle_subtitle_with_bazarr(issue_id, "tv", tvdb_id, title, season, episode)
            if bazarr_handled:
                # Close issue if Bazarr handled it successfully
                if CLOSE_ISSUES:
                    closed = await jelly_close(issue_id)
                    log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
                return
        
        # Fall back to traditional approach if Bazarr not available or failed
        log.info("Falling back to traditional subtitle handling for series %s", series_id)
    
    # Confirm-import mode applies only to the replace buckets (audio/video/subtitle):
    # an "other"/search-only bucket replaces nothing, so there's no import to await
    # and it falls through to the normal comment+close below. NB Bazarr-handled
    # subtitle fixes return early above and always close at action time (a Bazarr
    # search produces no Sonarr import to wait for).
    confirm = cfg.CONFIRM_REPLACEMENT_IMPORT and bucket in ("audio", "video", "subtitle")
    already_pending = confirm and (series_id, season, episode) in _PENDING_IMPORTS

    # Delete + re-search — unless a remediation for this exact episode is already in
    # flight (same download in progress); re-deleting it would be wasteful, so we
    # just attach this issue to the existing pending entry below.
    if not already_pending:
        removed = 0
        if bucket in ("audio", "video", "subtitle"):
            log.info("Deleting episode files for series %s, episodes %s", series_id, episode_ids)
            removed = await S.delete_episodefiles(series_id, episode_ids)
            log.info("Deleted %s episode files", removed)
        log.info("Triggering search for series %s episodes %s", series_id, episode_ids)
        await S.trigger_episode_search(episode_ids)

    # Confirm-import mode: don't claim success/close yet. Register the issue as
    # pending and post an interim comment; the Sonarr "On Import" webhook
    # (handle_sonarr_import) finalizes it once the replacement is actually on disk.
    if confirm:
        _register_pending_import(series_id, season, episode, issue_id, title)
        if COMMENT_ON_ACTION:
            msg = MSG_TV_SEARCHING.format(title=title, season=season, episode=episode)
            await jelly_comment(issue_id, f"{PREFIX} {msg}")
        await notify(f"Remediarr - TV", f"{title} S{season:02d}E{episode:02d}: re-download started; awaiting import")
        return

    # Default: comment success + close at search time.
    msg = MSG_TV_SUCCESS.format(title=title, season=season, episode=episode)
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {msg}")
    if CLOSE_ISSUES:
        closed = await jelly_close(issue_id)
        log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")

    await notify(f"Remediarr - TV", f"{title} S{season:02d}E{episode:02d}: fixed")

async def handle_sonarr_import(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sonarr "On Import" webhook entry (POST /webhook/sonarr).

    Only acts when CONFIRM_REPLACEMENT_IMPORT armed an issue: matches the imported
    episode against the pending map and, on a hit, posts the success comment +
    closes the Seerr issue. Any import with no pending issue is a no-op.
    """
    keys = _sonarr_import_keys(payload)
    if not keys:
        return {"ok": True, "detail": "ignored: not an import event"}

    handled = 0
    for key in keys:
        # Claim the entry atomically (pop) so a duplicate webhook — Sonarr fires
        # both "On Import" and "On Import Complete" for the same file — sees None
        # and no-ops instead of double-closing.
        pending = _PENDING_IMPORTS.pop(key, None)
        if not pending:
            continue
        _, season, episode = key
        title = pending["title"]
        issue_ids = sorted(pending["issue_ids"])
        log.info("Sonarr import confirms %d issue(s) for %s S%02dE%02d — finalizing",
                 len(issue_ids), title, season, episode)
        # Finalize each issue independently: a Seerr error on one (jelly_comment
        # raises on 4xx/5xx) must not drop the others or abort sibling keys. Only
        # the issues that failed get re-armed, so we never lose an issue and never
        # re-close one that already closed.
        failed = set()
        for issue_id in issue_ids:
            try:
                if COMMENT_ON_ACTION:
                    msg = MSG_TV_IMPORTED.format(title=title, season=season, episode=episode)
                    await jelly_comment(issue_id, f"{PREFIX} {msg}")
                if CLOSE_ISSUES:
                    closed = await jelly_close(issue_id)
                    log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
            except Exception as e:
                log.warning("Finalize failed for issue %s (%s S%02dE%02d); re-arming: %s",
                            issue_id, title, season, episode, e)
                failed.add(issue_id)
        if failed:
            _PENDING_IMPORTS[key] = {"issue_ids": failed, "title": title}
        if len(failed) < len(issue_ids):
            await notify(f"Remediarr - TV", f"{title} S{season:02d}E{episode:02d}: replacement imported")
            handled += 1

    if handled:
        return {"ok": True, "detail": f"import handled: {handled}"}
    return {"ok": True, "detail": "ignored: no pending issue for this import"}

async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main webhook entry. We always fetch the issue to normalize fields."""
    # Issue id (from payload or fail)
    raw_issue = (payload.get("issue") or {})
    issue_id = raw_issue.get("issue_id") or payload.get("issue_id")
    try:
        issue_id = int(issue_id)
    except Exception:
        issue_id = None

    if not issue_id:
        log.info("Webhook missing issue_id; ignoring. Payload keys: %s", list(payload.keys()))
        return {"ok": True, "detail": "ignored: no issue_id"}

    log.info("Processing issue %s", issue_id)

    # Skip if issue is already resolved to prevent processing resolved issue webhooks
    if (payload.get("issue") or {}).get("issue_status") == "RESOLVED":
        log.info("Issue %s already resolved, skipping", issue_id)
        return {"ok": True, "detail": "ignored: issue already resolved"}

    # Fetch the full issue details and merge with payload
    issue = await jelly_fetch_issue(issue_id)
    
    # Create enriched payload with both original payload and fetched issue data
    enriched_payload = {
        **payload,
        "issue": {**(payload.get("issue") or {}), **issue},
        "media": {**(payload.get("media") or {}), **(issue.get("media") or {})},
    }
    
    # Extract canonical context from enriched data
    media = enriched_payload.get("media") or {}
    media_type = (media.get("mediaType") or media.get("type") or "").lower()
    tmdb = media.get("tmdbId")
    tvdb = media.get("tvdbId")

    log.info("Issue context: media_type=%s, tmdb=%s, tvdb=%s", media_type, tmdb, tvdb)

    # Last human comment & bucket
    last = await jelly_last_human_comment(issue_id)
    log.info("Jellyseerr: last human comment on issue %s: %r", issue_id, last)
    
    # Skip if our own comment (prevent loops)
    if is_our_comment(last):
        log.info("Last comment is ours; skipping.")
        return {"ok": True, "detail": "ignored: our comment"}
    
    # Skip if this comment was already processed (prevent comment-triggered loops)
    comment_msg = (payload.get("comment") or {}).get("comment_message", "")
    if is_our_comment(comment_msg):
        log.info("Current comment is ours; skipping.")
        return {"ok": True, "detail": "ignored: processing our own comment"}

    # Bucket: ISSUE_TYPE_AS_BUCKET => the Seerr issue TYPE drives the action and
    # the comment is ignored; otherwise use the comment keyword scan (default).
    if cfg.ISSUE_TYPE_AS_BUCKET:
        bucket = _bucket_from_issue_type(enriched_payload.get("issue") or {})
        log.info("Issue-type mode: issue_type -> bucket=%s", bucket)
    else:
        bucket = _bucket_for(last, media_type)
        log.info("Keyword scan: %r -> bucket=%s", last, bucket)

    # No bucket → coach the user if coaching is enabled
    if not bucket:
        # In issue-type mode the comment/keywords are irrelevant; don't post the
        # keyword-coaching tip (it would be misleading).
        if cfg.ISSUE_TYPE_AS_BUCKET:
            log.info("Issue-type mode: no usable issue type; no action")
            return {"ok": True, "detail": "ignored: no usable issue type"}
        log.info("No actionable keywords found")
        
        # Coaching: suggest keywords when none match, based on issue type
        if COMMENT_ON_ACTION and issue_id:
            # Get the issue type from the enriched payload
            issue_type = (enriched_payload.get("issue") or {}).get("issue_type", "").lower()
            
            if media_type == "movie":
                if issue_type == "video":
                    keywords = list(MOV_VIDEO)[:5]
                elif issue_type == "audio":
                    keywords = list(MOV_AUDIO)[:5]
                elif issue_type == "subtitle":
                    keywords = list(MOV_SUBS)[:5]
                elif issue_type == "other":
                    keywords = list(MOV_OTHER)[:5]
                else:
                    # If issue type unknown, show a mix but prioritize common ones
                    keywords = list(MOV_VIDEO)[:2] + list(MOV_AUDIO)[:2] + list(MOV_WRONG)[:2]
                    
            elif media_type in ("tv", "series"):
                if issue_type == "video":
                    keywords = list(TV_VIDEO)[:5]
                elif issue_type == "audio":
                    keywords = list(TV_AUDIO)[:5]
                elif issue_type == "subtitle":
                    keywords = list(TV_SUBS)[:5]
                elif issue_type == "other":
                    keywords = list(TV_OTHER)[:5]
                else:
                    # If issue type unknown, show a mix but prioritize common ones
                    keywords = list(TV_VIDEO)[:2] + list(TV_AUDIO)[:2] + list(TV_SUBS)[:2]
            else:
                keywords = ["specific issue keywords"]
            
            if keywords:
                keyword_list = "', '".join(keywords)
                coach_msg = f"{PREFIX} Tip for {issue_type} issues: Include keywords like '{keyword_list}' for automatic fixes."
            else:
                coach_msg = f"{PREFIX} Tip: Include specific {issue_type} issue keywords for automatic fixes."
            
            log.info("Posting coaching comment for missing %s keywords", issue_type)
            await jelly_comment(issue_id, coach_msg)
        
        return {"ok": True, "detail": "ignored: no actionable keywords"}

    # Cooldown guard
    if _under_cooldown(issue_id):
        return {"ok": True, "detail": "ignored: cooldown"}

    # === MOVIES ===
    if media_type == "movie":
        if not tmdb:
            log.info("Movie issue lacks TMDB; skipping.")
            return {"ok": True, "detail": "ignored: missing tmdb id"}

        movie = await R.get_movie_by_tmdb(int(tmdb))
        if not movie:
            log.info("Radarr: movie not found locally; skipping.")
            return {"ok": True, "detail": "ignored: movie not in radarr"}

        log.info("Processing movie %s (%s) with bucket: %s", movie["id"], movie.get("title"), bucket)
        
        await _handle_movie(issue_id, movie, bucket)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"movie handled: {bucket}"}

    # === TV ===
    elif media_type in ("tv", "series"):
        if not tvdb:
            log.info("TV issue missing tvdbId; skipping.")
            return {"ok": True, "detail": "ignored: missing tvdb id"}

        try:
            # Use the robust extraction method from original app.py with enriched payload
            # episode=0 is the sentinel meaning "all episodes in season"
            series_id, series, season, episode = await _tv_episode_from_payload(enriched_payload)
            log.info("Successfully extracted TV context: series_id=%s, S%02d E%s",
                     series_id, season, "all" if episode == 0 else f"{episode:02d}")
        except (ValueError, Exception) as e:
            log.info("TV extraction failed: %s", str(e))
            return {"ok": True, "detail": f"ignored: {str(e)}"}

        if episode == 0:
            # Check issue description + comments for a specific episode list before nuking the whole season
            text_to_scan = " ".join([
                str(enriched_payload.get("message") or ""),
                str((enriched_payload.get("issue") or {}).get("message") or ""),
                str((enriched_payload.get("comment") or {}).get("comment_message") or ""),
            ])
            specific_eps = _parse_episode_list_from_text(text_to_scan, known_season=season)

            if specific_eps:
                log.info("Found specific episodes in text for S%02d: %s — handling individually", season, specific_eps)
                await _handle_tv_specific_episodes(issue_id, series, season, specific_eps, bucket)
                _bump_cooldown(issue_id)
                return {"ok": True, "detail": f"tv specific episodes handled: {bucket}"}

            log.info("Processing TV series %s (%s) S%02d (all episodes) with bucket: %s",
                     series_id, series.get("title"), season, bucket)
            await _handle_tv_season(issue_id, series, season, bucket)
            _bump_cooldown(issue_id)
            return {"ok": True, "detail": f"tv season handled: {bucket}"}

        episode_ids = await S.episode_ids_for(series_id, season, episode)
        if not episode_ids:
            log.info("Sonarr: no episode ids for S%02dE%02d", season, episode)
            return {"ok": True, "detail": "ignored: episode not present"}

        log.info("Processing TV series %s (%s) S%02dE%02d with bucket: %s",
                 series_id, series.get("title"), season, episode, bucket)

        await _handle_tv(issue_id, series, season, episode, episode_ids, bucket)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"tv handled: {bucket}"}

    log.info("Unknown media_type=%r; ignoring.", media_type)
    return {"ok": True, "detail": "ignored: unknown media_type"}
