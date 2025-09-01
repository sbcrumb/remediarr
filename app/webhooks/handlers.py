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

log = logging.getLogger("remediarr")

# env/config
PREFIX = os.getenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]")
CLOSE_ISSUES = os.getenv("JELLYSEERR_CLOSE_ISSUES", "true").lower() == "true"

RADARR_VERIFY_GRAB_SEC = int(os.getenv("RADARR_VERIFY_GRAB_SEC", "60"))
RADARR_VERIFY_POLL_SEC = int(os.getenv("RADARR_VERIFY_POLL_SEC", "5"))
SONARR_VERIFY_GRAB_SEC = int(os.getenv("SONARR_VERIFY_GRAB_SEC", "60"))
SONARR_VERIFY_POLL_SEC = int(os.getenv("SONARR_VERIFY_POLL_SEC", "5"))

# Messages
MSG_MOVIE_REPLACED_AND_GRABBED = os.getenv(
    "MSG_MOVIE_REPLACED_AND_GRABBED",
    "{title}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass."
)
MSG_MOVIE_SEARCH_ONLY_GRABBED = os.getenv(
    "MSG_MOVIE_SEARCH_ONLY_GRABBED",
    "{title}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass."
)
MSG_TV_REPLACED_AND_GRABBED = os.getenv(
    "MSG_TV_REPLACED_AND_GRABBED",
    "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass."
)
MSG_TV_SEARCH_ONLY_GRABBED = os.getenv(
    "MSG_TV_SEARCH_ONLY_GRABBED",
    "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass."
)

# Keyword buckets (lower-cased sets)
def _csv(name: str) -> List[str]:
    return [s.strip().lower() for s in os.getenv(name, "").split(",") if s.strip()]

TV_AUDIO = set(_csv("TV_AUDIO_KEYWORDS"))
TV_VIDEO = set(_csv("TV_VIDEO_KEYWORDS"))
TV_SUBS  = set(_csv("TV_SUBTITLE_KEYWORDS"))
TV_OTHER = set(_csv("TV_OTHER_KEYWORDS"))
MOV_AUDIO = set(_csv("MOVIE_AUDIO_KEYWORDS"))
MOV_VIDEO = set(_csv("MOVIE_VIDEO_KEYWORDS"))
MOV_SUBS  = set(_csv("MOVIE_SUBTITLE_KEYWORDS"))
MOV_OTHER = set(_csv("MOVIE_OTHER_KEYWORDS"))
MOV_WRONG = set(_csv("MOVIE_WRONG_KEYWORDS"))

def _bucket_for(text: str, media_type: Optional[str]) -> Optional[str]:
    t = (text or "").lower()
    words = set(re.split(r"[^a-z0-9]+", t))
    # movie-first or tv-first both share the same buckets; we just reuse sets
    if words & (MOV_AUDIO | TV_AUDIO): return "audio"
    if words & (MOV_VIDEO | TV_VIDEO): return "video"
    if words & (MOV_SUBS  | TV_SUBS):  return "subtitle"
    if media_type == "movie" and (words & MOV_WRONG): return "wrong"
    if words & (MOV_OTHER | TV_OTHER): return "other"
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
    baseline = await R.latest_grab_timestamp(movie_id)
    # Trigger search
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
            await jelly_comment(issue_id, f"{PREFIX} {msg}")
            if CLOSE_ISSUES:
                await jelly_close(issue_id)
            return
        await asyncio.sleep(step)
        waited += step
    log.info("Radarr verify window elapsed (no new grab). Not closing issue %s.", issue_id)

async def _verify_sonarr_and_close(issue_id: int, series: Dict[str, Any], season: int, episode: int, removed_count: int, episode_ids: List[int]) -> None:
    sid = series["id"]
    title = series.get("title") or f"Series {sid}"
    baseline = await S.latest_grab_timestamp(sid, episode_ids)

    await S.trigger_episode_search(episode_ids)

    total = SONARR_VERIFY_GRAB_SEC
    step = max(2, SONARR_VERIFY_POLL_SEC)
    waited = 0
    while waited < total:
        if await S.has_new_grab_since(sid, episode_ids, baseline):
            msg = (MSG_TV_REPLACED_AND_GRABBED if removed_count > 0 else MSG_TV_SEARCH_ONLY_GRABBED).format(
                title=title, season=season, episode=episode)
            await jelly_comment(issue_id, f"{PREFIX} {msg}")
            if CLOSE_ISSUES:
                await jelly_close(issue_id)
            return
        await asyncio.sleep(step)
        waited += step
    log.info("Sonarr verify window elapsed (no new grab). Not closing issue %s.", issue_id)

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

    issue = await jelly_fetch_issue(issue_id)
    ctx = {
        **(payload or {}),
        **(raw_issue or {}),
        **(issue or {}),
    }
    # Extract canonical context from fetched issue
    ic = {
        "media_type": (issue.get("media") or {}).get("mediaType") or (issue.get("media") or {}).get("type"),
        "tmdb": (issue.get("media") or {}).get("tmdbId"),
        "tvdb": (issue.get("media") or {}).get("tvdbId"),
        "season": issue.get("affectedSeason"),
        "episode": issue.get("affectedEpisode"),
    }
    media_type = (str(ic["media_type"] or "")).lower() or None
    tmdb = ic["tmdb"]
    tvdb = ic["tvdb"]
    season = ic["season"]
    episode = ic["episode"]

    # Last human comment & bucket
    last = await jelly_last_human_comment(issue_id)
    log.info("Jellyseerr: last human comment on issue %s: %r", issue_id, last)
    if is_our_comment(last):
        log.info("Last comment is ours; skipping.")
        return {"ok": True, "detail": "ignored: our comment"}

    # If S/E still missing for TV, try parse from comment text
    if media_type == "tv" and (not season or not episode):
        s, e = _parse_se_from_text(last)
        if s and e:
            season, episode = s, e

    # Bucket
    bucket = _bucket_for(last, media_type)
    log.info("Keyword scan: %r -> bucket=%s", last, bucket)

    # No bucket → do nothing
    if not bucket or bucket == "other":
        return {"ok": True, "detail": "ignored: no actionable keywords"}

    # Cooldown guard
    if _under_cooldown(issue_id):
        return {"ok": True, "detail": "ignored: cooldown"}

    # === MOVIES ===
    if media_type == "movie":
        if not (tmdb or (issue.get("media") or {}).get("imdbId")):
            log.info("Movie issue lacks TMDB/IMDB; skipping.")
            return {"ok": True, "detail": "ignored: missing ids"}

        movie = None
        if tmdb:
            movie = await R.get_movie_by_tmdb(int(tmdb))
        if not movie:
            imdb = (issue.get("media") or {}).get("imdbId")
            if imdb:
                movie = await R.get_movie_by_imdb(imdb)
        if not movie:
            log.info("Radarr: movie not found locally; skipping.")
            return {"ok": True, "detail": "ignored: movie not in radarr"}

        removed = 0
        if bucket in ("audio", "video", "subtitle", "wrong"):
            removed = await R.delete_moviefiles(movie["id"])

        await _verify_radarr_and_close(issue_id, movie, removed)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"movie handled: {bucket}"}

    # === TV ===
    if media_type in ("tv", "series"):
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

        removed = 0
        if bucket in ("audio", "video", "subtitle"):
            removed = await S.delete_episodefiles(series["id"], episode_ids)

        await _verify_sonarr_and_close(issue_id, series, int(season), int(episode), removed, episode_ids)
        _bump_cooldown(issue_id)
        return {"ok": True, "detail": f"tv handled: {bucket}"}

    log.info("Unknown media_type=%r; ignoring.", media_type)
    return {"ok": True, "detail": "ignored: unknown media_type"}
