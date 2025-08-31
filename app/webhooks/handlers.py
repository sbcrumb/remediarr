import asyncio
import hashlib
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from app.config import (
    log,
    COMMENT_PREFIX,
    # env-driven keywords
    MOV_VIDEO, MOV_AUDIO, MOV_SUBTITLE, MOV_OTHER, MOV_WRONG,
    TV_AUDIO, TV_VIDEO, TV_SUBTITLE, TV_OTHER,
    # env-driven messages
    JELLYSEERR_CLOSE_ISSUES, JELLYSEERR_CLOSE_MESSAGE,
    MSG_MOVIE_REPLACED_AND_GRABBED, MSG_TV_REPLACED_AND_GRABBED,
    MSG_MOVIE_SEARCH_ONLY_GRABBED, MSG_TV_SEARCH_ONLY_GRABBED,
)
from app.services.jellyseerr import jelly_comment, jelly_close, jelly_fetch_issue
from app.services.radarr import (
    radarr_get_by_tmdb, radarr_mark_last_grab_failed, radarr_delete_all_files,
    radarr_search, radarr_queue_has_tmdb,
)
from app.services.sonarr import (
    sonarr_series_by_tvdb, sonarr_find_episode, sonarr_delete_episode_file,
    sonarr_search_episode, sonarr_queue_has_episode,
)

# ---------- helpers ----------

def _ensure_prefixed(msg: str) -> str:
    """Guarantee the hard-coded bot prefix is present exactly once."""
    m = (msg or "").strip()
    if not m:
        return m
    return m if m.startswith(COMMENT_PREFIX) else f"{COMMENT_PREFIX} {m}"

_seen: dict[str, float] = {}
SEEN_TTL_SEC = 5.0

def _dedupe_key(payload: Dict[str, Any]) -> str:
    event = str(payload.get("event") or "").lower()
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    parts = [
        event,
        str(issue.get("issue_id") or ""),
        str(comment.get("comment_id") or comment.get("id") or ""),
        str(issue.get("updatedAt") or ""),
        str(comment.get("createdAt") or ""),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def _is_dupe(payload: Dict[str, Any]) -> bool:
    now = time.time()
    key = _dedupe_key(payload)
    ts = _seen.get(key, 0.0)
    if now - ts < SEEN_TTL_SEC:
        return True
    _seen[key] = now
    if len(_seen) > 5000:
        cutoff = now - SEEN_TTL_SEC
        for k, v in list(_seen.items()):
            if v < cutoff:
                _seen.pop(k, None)
    return False

def _has_kw(text: str, kws: list[str]) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in kws)

def _payload_text(payload: Dict[str, Any]) -> str:
    p = payload or {}
    issue = p.get("issue") or {}
    comment = p.get("comment") or {}
    parts = [
        str(p.get("subject") or ""),
        str(p.get("message") or ""),
        str(issue.get("issue_type") or ""),
        str(issue.get("issue_status") or ""),
        str(comment.get("comment_message") or ""),
    ]
    return " ".join([x for x in parts if x]).strip()

async def _movie_ctx(payload: Dict[str, Any]) -> Tuple[int, str, Optional[int]]:
    media = payload.get("media") or {}
    tmdb = media.get("tmdbId") or media.get("tmdbid")
    if not tmdb:
        raise HTTPException(400, "Missing tmdbId")
    movie = await radarr_get_by_tmdb(int(tmdb))
    if not movie:
        raise HTTPException(404, "Movie not found in Radarr")
    return movie["id"], movie.get("title", f"tmdb-{tmdb}"), int(tmdb)

async def _tv_ctx(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], int, int]:
    media = payload.get("media") or {}
    tvdb = media.get("tvdbId") or media.get("tvdbid")
    if not tvdb:
        raise HTTPException(400, "Missing tvdbId")
    series = await sonarr_series_by_tvdb(int(tvdb))
    if not series:
        raise HTTPException(404, "Series not found in Sonarr")
    issue = payload.get("issue") or {}
    season = issue.get("affected_season") or issue.get("season")
    episode = issue.get("affected_episode") or issue.get("episode")
    if season is None or episode is None:
        s, e = await jelly_fetch_issue(issue.get("issue_id"))
        season = season or s
        episode = episode or e
    if season is None or episode is None:
        raise HTTPException(400, "Missing season/episode")
    return series["id"], series, int(season), int(episode)

# ---------- main handler ----------

async def handle_jelly_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    if _is_dupe(payload):
        return {"ok": True, "skipped": True, "reason": "dupe debounce"}

    event = (payload.get("event") or "").lower()
    issue = payload.get("issue") or {}
    issue_id = issue.get("issue_id")
    status = (issue.get("issue_status") or "").lower()
    media = payload.get("media") or {}
    media_type = (media.get("media_type") or media.get("mediaType") or "").lower()

    # Only act on OPEN issues
    if status and status != "open":
        return {"ok": True, "skipped": True, "reason": f"status {status}"}

    # Ignore our own comments by fixed prefix (users cannot change it)
    text = _payload_text(payload)
    if COMMENT_PREFIX.lower() in text.lower():
        return {"ok": True, "skipped": True, "reason": "own prefix"}

    # ===== Movies =====
    if media_type == "movie":
        # 'wrong movie' has priority
        if _has_kw(text, MOV_WRONG):
            movie_id, title, tmdb = await _movie_ctx(payload)
            await radarr_mark_last_grab_failed(movie_id)
            deleted = await radarr_delete_all_files(movie_id)
            await radarr_search(movie_id)

            if await radarr_queue_has_tmdb(tmdb):
                await jelly_comment(issue_id, _ensure_prefixed(MSG_MOVIE_REPLACED_AND_GRABBED.format(title=title)))
                if JELLYSEERR_CLOSE_ISSUES:
                    ok = await jelly_close(issue_id)
                    cm = _ensure_prefixed(JELLYSEERR_CLOSE_MESSAGE)
                    if ok and cm:
                        await jelly_comment(issue_id, cm)
                return {"ok": True, "action": "movie_wrong", "deleted": deleted, "verified": True}
            return {"ok": True, "action": "movie_wrong", "deleted": deleted, "verified": False}

        # regular categories
        mapping = {
            "audio": MOV_AUDIO,
            "video": MOV_VIDEO,
            "subtitle": MOV_SUBTITLE,
            "other": MOV_OTHER,
        }
        match = next((cat for cat, kws in mapping.items() if _has_kw(text, kws)), None)
        if match:
            movie_id, title, tmdb = await _movie_ctx(payload)
            await radarr_mark_last_grab_failed(movie_id)
            deleted = await radarr_delete_all_files(movie_id)
            await radarr_search(movie_id)

            if await radarr_queue_has_tmdb(tmdb):
                await jelly_comment(issue_id, _ensure_prefixed(MSG_MOVIE_REPLACED_AND_GRABBED.format(title=title)))
                if JELLYSEERR_CLOSE_ISSUES:
                    ok = await jelly_close(issue_id)
                    cm = _ensure_prefixed(JELLYSEERR_CLOSE_MESSAGE)
                    if ok and cm:
                        await jelly_comment(issue_id, cm)
                return {"ok": True, "action": f"movie_{match}", "deleted": deleted, "verified": True}
            return {"ok": True, "action": f"movie_{match}", "deleted": deleted, "verified": False}

    # ===== TV =====
    if media_type == "tv":
        mapping = {
            "audio": TV_AUDIO,
            "video": TV_VIDEO,
            "subtitle": TV_SUBTITLE,  # no delete for subtitles
            "other": TV_OTHER,
        }
        match = next((cat for cat, kws in mapping.items() if _has_kw(text, kws)), None)
        if match:
            series_id, series, season, episode = await _tv_ctx(payload)
            title = series.get("title", "TV Show")
            ep = await sonarr_find_episode(series_id, season, episode)
            if not ep:
                raise HTTPException(404, "Episode not found in Sonarr")

            deleted = 0
            if match != "subtitle" and ep.get("episodeFileId"):
                await sonarr_delete_episode_file(ep["episodeFileId"])
                deleted = 1
            await sonarr_search_episode(ep["id"])

            if await sonarr_queue_has_episode(series_id, season, episode):
                msg_tpl = MSG_TV_SEARCH_ONLY_GRABBED if match == "subtitle" else MSG_TV_REPLACED_AND_GRABBED
                msg = _ensure_prefixed(msg_tpl.format(title=title, season=season, episode=episode))
                await jelly_comment(issue_id, msg)
                if JELLYSEERR_CLOSE_ISSUES:
                    ok = await jelly_close(issue_id)
                    cm = _ensure_prefixed(JELLYSEERR_CLOSE_MESSAGE)
                    if ok and cm:
                        await jelly_comment(issue_id, cm)
                return {"ok": True, "action": f"tv_{match}", "deleted": deleted, "verified": True}
            return {"ok": True, "action": f"tv_{match}", "deleted": deleted, "verified": False}

    return {"ok": True, "skipped": True, "reason": "no rules matched"}
