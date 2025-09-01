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
    words = set(re.split(r"[^a-z0-9]+", t))
    
    # Check for wrong movie first (movie-specific)
    if media_type == "movie" and (words & MOV_WRONG):
        return "wrong"
    
    # Check other buckets
    if words & (MOV_AUDIO | TV_AUDIO): 
        return "audio"
    if words & (MOV_VIDEO | TV_VIDEO): 
        return "video"
    if words & (MOV_SUBS | TV_SUBS):  
        return "subtitle"
    if words & (MOV_OTHER | TV_OTHER): 
        return "other"
    
    return None

_sxxexx = re.compile(r"[sS](\d{1,2})[ .-]*[eE](\d{1,2})")

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
    
    # Wait/poll for a *new* grabbed after baseline
    total = RADARR_VERIFY_GRAB_SEC
    step = max(2, RADARR_VERIFY_POLL_SEC)
    waited = 0
    while waited < total:
        if await R.has_new_grab_since(movie_id, baseline):
            # Success → one closing comment + resolve
            msg = (MSG_MOVIE_REPLACED_AND_GRABBED if removed_count > 0 else MSG_MOVIE_SEARCH_ONLY_GRABBED).format(
                title=title)
            if COMMENT_ON_ACTION:
                await jelly_comment(issue_id, f"{PREFIX} {msg}")
            if CLOSE_ISSUES:
                await jelly_close(issue_id)
            await notify(f"Remediarr - Movie Fixed", f"{PREFIX} {msg}")
            return
        await asyncio.sleep(step)
        waited += step
    
    log.info("Radarr verify window elapsed (no new grab). Not closing issue %s.", issue_id)
    # Still comment that we tried
    if COMMENT_ON_ACTION:
        await jelly_comment(issue_id, f"{PREFIX} {title}: triggered search but no new download yet. Please check back later.")

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

    # Fetch the full issue details
    issue = await jelly_fetch_issue(issue_id)
    
    # Extract canonical context from fetched issue
    media = issue.get("media") or {}
    media_type = (media.get("mediaType") or media.get("type") or "").lower()
    tmdb = media.get("tmdbId")
    tvdb = media.get("tvdbId")
    season = issue.get("affectedSeason")
    episode = issue.get("affectedEpisode")

    log.info("Issue context: media_type=%s, tmdb=%s, tvdb=%s, season=%s, episode=%s", 
             media_type, tmdb, tvdb, season, episode)

    # Last human comment & bucket
    last = await jelly_last_human_comment(issue_id)
    log.info("Jellyseerr: last human comment on issue %s: %r", issue_id, last)
    
    if is_our_comment(last):
        log.info("Last comment is ours; skipping.")
        return {"ok": True, "detail": "ignored: our comment"}

    # If S/E still missing for TV, try parse from comment text
    if media_type in ("tv", "series") and (not season or not episode):
        s, e = _parse_se_from_text(last)
        if s and e:
            season, episode = s, e
            log.info("Parsed S/E from comment: S%02dE%02d", season, episode)

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
        if not (tvdb and season and episode):
            log.info("Series missing season/episode or tvdb; not acting to avoid season-wide search.")
            return {"ok": True, "detail": "ignored: insufficient TV context"}

        series = await S.get_series_by_tvdb(int(tvdb))
        if not series:
            log.info("Sonarr: series not found for tvdb=%s", tvdb)
            return {"ok": True, "detail": "ignored: series not in sonarr"}

        episode_ids = await S.episode_ids_for(series["id"], int(season), int(episode))
        if not episode_ids:
            log.info("Sonarr: no episode ids for S%02dE%02d", int(season), int(episode))
            return {"ok": True, "detail": "ignored: episode not present"}

        log.info("Processing TV series %s (%s) S%02dE%02d with bucket: %s", 
                 series["id"], series.get("title"), season, episode, bucket)

        removed = 0
        if bucket in ("audio", "video", "subtitle"):
            log.info("Deleting episode files for series %s, episodes %s", series["id"], episode_ids)
            removed = await S.delete_episodefiles(series["id"], episode_ids)
            log.info("Deleted %s episode files", removed)

        # Start the verification and close process
        await _verify_sonarr_and_close(issue_id, series, int(season), int(episode), removed, episode_ids)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"tv handled: {bucket}", "removed": removed}

    log.info("Unknown media_type=%r; ignoring.", media_type)
    return {"ok": True, "detail": "ignored: unknown media_type"}