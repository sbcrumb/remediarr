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
from app.services.notify import notify

log = logging.getLogger("remediarr")

# env/config
PREFIX = os.getenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]")
CLOSE_ISSUES = os.getenv("JELLYSEERR_CLOSE_ISSUES", "true").lower() == "true"
COMMENT_ON_ACTION = os.getenv("JELLYSEERR_COMMENT_ON_ACTION", "true").lower() == "true"

RADARR_VERIFY_GRAB_SEC = int(os.getenv("RADARR_VERIFY_GRAB_SEC", "60"))
RADARR_VERIFY_POLL_SEC = int(os.getenv("RADARR_VERIFY_POLL_SEC", "5"))
SONARR_VERIFY_GRAB_SEC = int(os.getenv("SONARR_VERIFY_GRAB_SEC", "60"))
SONARR_VERIFY_POLL_SEC = int(os.getenv("SONARR_VERIFY_POLL_SEC", "5"))

# Messages
MSG_MOVIE_REPLACED_AND_GRABBED = os.getenv(
    "MSG_MOVIE_REPLACED_AND_GRABBED",
    "{title}: replaced file; new download grabbed. Closing this issue. If anything's still off, comment and I'll take another pass."
)
MSG_MOVIE_SEARCH_ONLY_GRABBED = os.getenv(
    "MSG_MOVIE_SEARCH_ONLY_GRABBED",
    "{title}: new download grabbed. Closing this issue. If anything's still off, comment and I'll take another pass."
)
MSG_TV_REPLACED_AND_GRABBED = os.getenv(
    "MSG_TV_REPLACED_AND_GRABBED",
    "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue. If anything's still off, comment and I'll take another pass."
)
MSG_TV_SEARCH_ONLY_GRABBED = os.getenv(
    "MSG_TV_SEARCH_ONLY_GRABBED",
    "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue. If anything's still off, comment and I'll take another pass."
)

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
                if s_found is None and _key_looks_like(k, "season"):
                    sv = _maybe_int_from_obj(v)
                    s_found = sv if sv is not None else s_found
                if e_found is None and _key_looks_like(k, "episode"):
                    ev = _maybe_int_from_obj(v)
                    e_found = ev if ev is not None else e_found
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

async def _tv_episode_from_payload(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], int, int]:
    issue = payload.get("issue") or {}
    media = payload.get("media") or {}
    comment = payload.get("comment") or {}

    tvdb_id = media.get("tvdbId") or media.get("tvdbid")
    if not tvdb_id:
        raise ValueError("Missing tvdbId")

    # Try explicit keys first - handle both string and int values
    season_candidates = [
        issue.get("affected_season"), 
        issue.get("affectedSeason"),
        media.get("seasonNumber"), 
        media.get("season")
    ]
    episode_candidates = [
        issue.get("affected_episode"), 
        issue.get("affectedEpisode"),
        media.get("episodeNumber"), 
        media.get("episode")
    ]
    
    season = None
    episode = None
    
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
    
    # Extract episode  
    for candidate in episode_candidates:
        if candidate is not None:
            # Handle string values that might be numbers
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

    log.info("After explicit extraction: season=%s, episode=%s", season, episode)

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

    # Final fallback: fetch issue from Jellyseerr API
    if (season is None or episode is None) and issue.get("issue_id"):
        try:
            full_issue = await jelly_fetch_issue(int(issue.get("issue_id")))
            # Look for affectedSeason/affectedEpisode in the API response
            api_season = full_issue.get("affectedSeason")
            api_episode = full_issue.get("affectedEpisode")
            
            if api_season is not None and season is None:
                try:
                    season = int(api_season)
                    log.info("Found season from API: %s", season)
                except (ValueError, TypeError):
                    pass
                    
            if api_episode is not None and episode is None:
                try:
                    episode = int(api_episode)
                    log.info("Found episode from API: %s", episode)
                except (ValueError, TypeError):
                    pass
                    
        except Exception as e:
            log.warning("Failed to fetch issue details: %s", e)

    # Get series from Sonarr
    series = await S.get_series_by_tvdb(int(tvdb_id))
    if not series:
        raise ValueError("Series not found in Sonarr")
    
    if season is None or episode is None:
        raise ValueError(f"Missing season/episode after all extraction attempts: S{season}E{episode}")
    
    # Sanity check the values
    if season < 1 or season > 50:
        raise ValueError(f"Invalid season number: {season}")
    if episode < 1 or episode > 1000:
        raise ValueError(f"Invalid episode number: {episode}")
    
    return series["id"], series, int(season), int(episode)

def _parse_se_from_text(text: str) -> Tuple[Optional[int], Optional[int]]:
    m = _sxxexx.search(text or "")
    if not m:
        return (None, None)
    try:
        return (int(m.group(1)), int(m.group(2)))
    except Exception:
        return (None, None)

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

async def _verify_radarr_and_close(issue_id: int, movie: Dict[str, Any], removed_count: int) -> None:
    movie_id = movie["id"]
    title = movie.get("title") or movie.get("titleSlug") or f"Movie {movie_id}"
    
    # Get baseline before triggering search
    baseline = await R.latest_grab_timestamp(movie_id)
    log.info("Movie %s baseline grab timestamp: %s", movie_id, baseline)
    
    # Trigger search
    log.info("Triggering search for movie %s", movie_id)
    await R.trigger_search_movie(movie_id)
    
    # Give it a moment for the search to register before polling
    await asyncio.sleep(3)
    
    # Wait/poll for a *new* grabbed after baseline
    total = RADARR_VERIFY_GRAB_SEC
    step = max(3, RADARR_VERIFY_POLL_SEC)
    waited = 3  # Already waited 3 seconds above
    
    while waited < total:
        new_grab = await R.has_new_grab_since(movie_id, baseline)
        log.info("Movie %s grab check: waited=%ds, new_grab=%s", movie_id, waited, new_grab)
        
        if new_grab:
            # Success → one closing comment + resolve
            msg = (MSG_MOVIE_REPLACED_AND_GRABBED if removed_count > 0 else MSG_MOVIE_SEARCH_ONLY_GRABBED).format(
                title=title)
            if COMMENT_ON_ACTION:
                await jelly_comment(issue_id, f"{PREFIX} {msg}")
            if CLOSE_ISSUES:
                closed = await jelly_close(issue_id)
                log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
            await notify(f"Remediarr - Movie Fixed", f"{msg}")
            return
            
        await asyncio.sleep(step)
        waited += step
    
    log.info("Radarr verify window elapsed (no new grab). Not closing issue %s.", issue_id)
    # Don't comment on timeout - let the user know the search was triggered but wait longer

async def _verify_sonarr_and_close(issue_id: int, series: Dict[str, Any], season: int, episode: int, removed_count: int, episode_ids: List[int]) -> None:
    sid = series["id"]
    title = series.get("title") or f"Series {sid}"
    
    # Get baseline before triggering search
    baseline = await S.latest_grab_timestamp(sid, episode_ids)
    log.info("Series %s S%02dE%02d baseline grab timestamp: %s", sid, season, episode, baseline)

    # Trigger search
    log.info("Triggering search for series %s episodes %s", sid, episode_ids)
    await S.trigger_episode_search(episode_ids)

    total = SONARR_VERIFY_GRAB_SEC
    step = max(2, SONARR_VERIFY_POLL_SEC)
    waited = 0
    while waited < total:
        if await S.has_new_grab_since(sid, episode_ids, baseline):
            msg = (MSG_TV_REPLACED_AND_GRABBED if removed_count > 0 else MSG_TV_SEARCH_ONLY_GRABBED).format(
                title=title, season=season, episode=episode)
            if COMMENT_ON_ACTION:
                await jelly_comment(issue_id, f"{PREFIX} {msg}")
            if CLOSE_ISSUES:
                await jelly_close(issue_id)
            await notify(f"Remediarr - TV Fixed", f"{PREFIX} {msg}")
            return
        await asyncio.sleep(step)
        waited += step
    
    log.info("Sonarr verify window elapsed (no new grab). Not closing issue %s.", issue_id)
    # Still comment that we tried
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {title} S{season:02d}E{episode:02d}: triggered search but no new download yet. Please check back later.")

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
    
    if is_our_comment(last):
        log.info("Last comment is ours; skipping.")
        return {"ok": True, "detail": "ignored: our comment"}

    # Bucket
    bucket = _bucket_for(last, media_type)
    log.info("Keyword scan: %r -> bucket=%s", last, bucket)

    # No bucket → do nothing
    if not bucket:
        log.info("No actionable keywords found")
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

        removed = 0
        if bucket in ("audio", "video", "subtitle", "wrong"):
            log.info("Deleting movie files for movie %s", movie["id"])
            removed = await R.delete_moviefiles(movie["id"])
            log.info("Deleted %s movie files", removed)

        # Start the verification and close process
        await _verify_radarr_and_close(issue_id, movie, removed)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"movie handled: {bucket}", "removed": removed}

    # === TV ===
    elif media_type in ("tv", "series"):
        if not tvdb:
            log.info("TV issue missing tvdbId; skipping.")
            return {"ok": True, "detail": "ignored: missing tvdb id"}

        try:
            # Use the robust extraction method from original app.py with enriched payload
            series_id, series, season, episode = await _tv_episode_from_payload(enriched_payload)
            log.info("Successfully extracted TV context: series_id=%s, S%02dE%02d", series_id, season, episode)
        except (ValueError, Exception) as e:
            log.info("TV extraction failed: %s", str(e))
            return {"ok": True, "detail": f"ignored: {str(e)}"}

        episode_ids = await S.episode_ids_for(series_id, season, episode)
        if not episode_ids:
            log.info("Sonarr: no episode ids for S%02dE%02d", season, episode)
            return {"ok": True, "detail": "ignored: episode not present"}

        log.info("Processing TV series %s (%s) S%02dE%02d with bucket: %s", 
                 series_id, series.get("title"), season, episode, bucket)

        removed = 0
        if bucket in ("audio", "video", "subtitle"):
            log.info("Deleting episode files for series %s, episodes %s", series_id, episode_ids)
            removed = await S.delete_episodefiles(series_id, episode_ids)
            log.info("Deleted %s episode files", removed)

        # Start the verification and close process
        await _verify_sonarr_and_close(issue_id, series, season, episode, removed, episode_ids)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"tv handled: {bucket}", "removed": removed}

    log.info("Unknown media_type=%r; ignoring.", media_type)
    return {"ok": True, "detail": "ignored: unknown media_type"}