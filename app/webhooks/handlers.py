import os
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from app.domain.keywords import (
    TV_AUDIO, TV_VIDEO, TV_SUBTITLE, TV_OTHER,
    MOV_AUDIO, MOV_VIDEO, MOV_SUBTITLE, MOV_OTHER, MOV_WRONG
)
from app.services import jellyseerr
from app.services import radarr as R
from app.services import sonarr as S

# -------- Behavior & text (env-configurable) --------
COMMENT_PREFIX = os.getenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]").strip()
COMMENT_ON_ACTION = os.getenv("JELLYSEERR_COMMENT_ON_ACTION", "true").lower() == "true"
COACH = os.getenv("JELLYSEERR_COACH_REPORTERS", "true").lower() == "true"
AUTO_CLOSE = os.getenv("JELLYSEERR_CLOSE_ISSUES", "false").lower() == "true"
CLOSE_MESSAGE = os.getenv("JELLYSEERR_CLOSE_MESSAGE", "").strip()
COOLDOWN_SEC = int(os.getenv("REMEDIARR_ISSUE_COOLDOWN_SEC", "90"))

# One-pass RAM cooldown map to avoid ping-pong on comment webhooks
_last_action_at: dict[str, float] = {}


def _has_kw(text: str, kws: list[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in kws)


def _coach_list(kws: list[str], label: str) -> str:
    return ", ".join([f"'{k}'" for k in kws[:5]]) or f"'<add {label} keywords>'"


def _extract_title_year(subject: str, message: str) -> Tuple[Optional[str], Optional[int]]:
    blob = f"{subject} {message}"
    m = re.search(r"(.+?)\s*\((\d{4})\)", blob)
    if m:
        return m.group(1).strip(" -"), int(m.group(2))
    return None, None


async def _maybe_close(issue_id: Any) -> None:
    if not (AUTO_CLOSE and issue_id):
        return
    # Best-known working endpoint: /issue/{id}/resolved
    ok = await jellyseerr.comment_issue(issue_id, CLOSE_MESSAGE) if CLOSE_MESSAGE else True
    await jellyseerr._simple_request("POST", f"/api/v1/issue/{issue_id}/resolved")
    if not ok and CLOSE_MESSAGE:
        # comment failed earlier—try after close anyway (harmless if 4xx)
        await jellyseerr.comment_issue(issue_id, CLOSE_MESSAGE)


async def handle_jellyseerr(payload: Dict[str, Any]):
    """
    Minimal but robust handler:
    - TV: audio/video/subtitle/other
    - Movies: audio/video/subtitle/other + 'wrong movie'
    - Comment only once after verifying queue has the job
    """
    event = (payload.get("event") or "").lower()
    media = payload.get("media") or {}
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}

    issue_id = issue.get("issue_id")
    media_type = (media.get("media_type") or media.get("mediaType") or "").lower()
    issue_type = (issue.get("issue_type") or "").lower()

    subject = str(payload.get("subject") or "")
    text = " ".join([
        subject,
        str(issue.get("issue_type") or ""),
        str(issue.get("issue_status") or ""),
        str(payload.get("message") or ""),
        str(comment.get("comment_message") or "")
    ])

    # cooldown for comment-driven triggers
    now = datetime.utcnow().timestamp()
    if "comment" in event and issue_id:
        last = _last_action_at.get(str(issue_id), 0.0)
        if now - last < COOLDOWN_SEC:
            return {"ok": True, "skipped": True, "reason": f"cooldown {COOLDOWN_SEC}s"}

    # ignore our own comments by prefix
    if COMMENT_PREFIX and COMMENT_PREFIX.lower() in text.lower():
        return {"ok": True, "skipped": True, "reason": "own comment"}

    # -------- Coaching if no keywords --------
    if COACH and issue_id:
        if media_type == "tv":
            if issue_type == "audio" and not _has_kw(text, TV_AUDIO()):
                await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-fix TV audio: {_coach_list(TV_AUDIO(),'tv audio')}.")
                return {"ok": True, "skipped": True}
            if issue_type == "video" and not _has_kw(text, TV_VIDEO()):
                await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-fix TV video: {_coach_list(TV_VIDEO(),'tv video')}.")
                return {"ok": True, "skipped": True}
            if issue_type == "subtitle" and not _has_kw(text, TV_SUBTITLE()):
                await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-fix TV subtitles: {_coach_list(TV_SUBTITLE(),'tv subtitle')}.")
                return {"ok": True, "skipped": True}
            if issue_type == "other" and not _has_kw(text, TV_OTHER()):
                await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to trigger automation: {_coach_list(TV_OTHER(),'tv other')}.")
                return {"ok": True, "skipped": True}

        if media_type == "movie":
            if _has_kw(text, MOV_WRONG()):
                pass
            else:
                if issue_type == "audio" and not _has_kw(text, MOV_AUDIO()):
                    await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-handle movie audio: {_coach_list(MOV_AUDIO(),'movie audio')}.")
                    return {"ok": True, "skipped": True}
                if issue_type == "video" and not _has_kw(text, MOV_VIDEO()):
                    await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-handle movie video: {_coach_list(MOV_VIDEO(),'movie video')}.")
                    return {"ok": True, "skipped": True}
                if issue_type == "subtitle" and not _has_kw(text, MOV_SUBTITLE()):
                    await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-handle movie subtitles: {_coach_list(MOV_SUBTITLE(),'movie subtitle')}.")
                    return {"ok": True, "skipped": True}
                if issue_type == "other" and not _has_kw(text, MOV_OTHER()):
                    await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} Tip: include one of these keywords to auto-handle movie other: {_coach_list(MOV_OTHER(),'movie other')}.")
                    return {"ok": True, "skipped": True}

    # -------- Actions (keywords matched) --------
    if media_type == "tv":
        # Need tvdbId + season/episode (they're typically in payload, but may need fallback in your extended version)
        tvdb_id = media.get("tvdbId") or media.get("tvdbid")
        season = media.get("seasonNumber") or issue.get("affected_season") or issue.get("season")
        episode = media.get("episodeNumber") or issue.get("affected_episode") or issue.get("episode")
        if not (tvdb_id and season is not None and episode is not None):
            raise HTTPException(status_code=400, detail="Missing tvdbId/season/episode in payload")

        series = await S.get_series_by_tvdb(int(tvdb_id))
        if not series:
            raise HTTPException(status_code=404, detail="Series not found in Sonarr")

        # Subtitles: do NOT delete — just search (you can hook Bazarr later)
        if issue_type == "subtitle" and _has_kw(text, TV_SUBTITLE()):
            deleted, queued, _ = await S.delete_and_search_episode(
                int(series["id"]), int(season), int(episode)
            )  # delete_if_exists + search (delete may be False)
            if COMMENT_ON_ACTION and issue_id:
                msg = f"{COMMENT_PREFIX} {series.get('title','TV Show')} S{int(season):02d}E{int(episode):02d}: subtitle fix search started."
                await jellyseerr.comment_issue(issue_id, msg)
            if AUTO_CLOSE and issue_id and queued:
                await _maybe_close(issue_id)
            _last_action_at[str(issue_id)] = now
            return {"ok": True, "action": "tv_subtitle", "queued": queued}

        # audio/video/other → delete existing file then search
        if issue_type in ("audio", "video", "other") and (
            (issue_type == "audio" and _has_kw(text, TV_AUDIO())) or
            (issue_type == "video" and _has_kw(text, TV_VIDEO())) or
            (issue_type == "other" and _has_kw(text, TV_OTHER()))
        ):
            deleted, queued, _ = await S.delete_and_search_episode(
                int(series["id"]), int(season), int(episode)
            )
            if COMMENT_ON_ACTION and issue_id and queued:
                msg = f"{COMMENT_PREFIX} {series.get('title','TV Show')} S{int(season):02d}E{int(episode):02d}: replaced (deleted={int(deleted)}), new download started."
                await jellyseerr.comment_issue(issue_id, msg)
            if AUTO_CLOSE and issue_id and queued:
                await _maybe_close(issue_id)
            _last_action_at[str(issue_id)] = now
            return {"ok": True, "action": f"tv_{issue_type}", "deleted": deleted, "queued": queued}

    if media_type == "movie":
        tmdb_id = media.get("tmdbId") or media.get("tmdbid")
        if not tmdb_id:
            # best effort: try to extract title/year (not used here to search; Radarr needs id)
            _extract_title_year(subject, payload.get("message") or "")
            raise HTTPException(status_code=400, detail="Missing tmdbId for movie action")

        movie = await R.get_movie_by_tmdb(int(tmdb_id))
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found in Radarr")
        movie_id = int(movie["id"])
        title = movie.get("title", f"tmdb-{tmdb_id}")

        # wrong movie
        if _has_kw(text, MOV_WRONG()):
            deleted, queued = await R.fail_last_grab_delete_files_and_search(movie_id)
            if COMMENT_ON_ACTION and issue_id and queued:
                await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} {title}: wrong movie fixed (deleted={deleted}), new download started.")
            if AUTO_CLOSE and issue_id and queued:
                await _maybe_close(issue_id)
            _last_action_at[str(issue_id)] = now
            return {"ok": True, "action": "movie_wrong", "deleted": deleted, "queued": queued}

        # audio/video/subtitle/other
        if (
            (issue_type == "audio" and _has_kw(text, MOV_AUDIO())) or
            (issue_type == "video" and _has_kw(text, MOV_VIDEO())) or
            (issue_type == "subtitle" and _has_kw(text, MOV_SUBTITLE())) or
            (issue_type == "other" and _has_kw(text, MOV_OTHER()))
        ):
            deleted, queued = await R.fail_last_grab_delete_files_and_search(movie_id)
            if COMMENT_ON_ACTION and issue_id and queued:
                await jellyseerr.comment_issue(issue_id, f"{COMMENT_PREFIX} {title}: replaced (deleted={deleted}), new download started.")
            if AUTO_CLOSE and issue_id and queued:
                await _maybe_close(issue_id)
            _last_action_at[str(issue_id)] = now
            return {"ok": True, "action": f"movie_{issue_type}", "deleted": deleted, "queued": queued}

    return {"ok": True, "skipped": True, "reason": "no rules matched"}
