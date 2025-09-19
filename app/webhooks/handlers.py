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

async def _tv_episode_from_payload(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], int, int]:
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
    
    if season is None or episode is None:
        raise ValueError(f"Missing season/episode after all extraction attempts: S{season}E{episode}")
    
    # Sanity check the values
    if season < 1 or season > 50:
        raise ValueError(f"Invalid season number: {season}")
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
    
    # Delete files if needed (traditional approach)
    removed = 0
    if bucket in ("audio", "video", "subtitle"):
        log.info("Deleting episode files for series %s, episodes %s", series_id, episode_ids)
        removed = await S.delete_episodefiles(series_id, episode_ids)
        log.info("Deleted %s episode files", removed)

    # Trigger search
    log.info("Triggering search for series %s episodes %s", series_id, episode_ids)
    await S.trigger_episode_search(episode_ids)
    
    # Comment and close
    msg = MSG_TV_SUCCESS.format(title=title, season=season, episode=episode)
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {msg}")
    if CLOSE_ISSUES:
        closed = await jelly_close(issue_id)
        log.info("Issue %s close attempt: %s", issue_id, "success" if closed else "failed")
    
    await notify(f"Remediarr - TV", f"{title} S{season:02d}E{episode:02d}: fixed")

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

    # Bucket
    bucket = _bucket_for(last, media_type)
    log.info("Keyword scan: %r -> bucket=%s", last, bucket)

    # No bucket → coach the user if coaching is enabled
    if not bucket:
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

        await _handle_tv(issue_id, series, season, episode, episode_ids, bucket)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"tv handled: {bucket}"}

    log.info("Unknown media_type=%r; ignoring.", media_type)
    return {"ok": True, "detail": "ignored: unknown media_type"}
