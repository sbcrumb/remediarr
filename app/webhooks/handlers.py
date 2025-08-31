from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from app.config import cfg, BOT_PREFIX
from app.logging import log
from app.services.jellyseerr import jelly_comment, jelly_close, is_our_comment, jelly_fetch_issue
from app.services import sonarr as S
from app.services import radarr as R


# ---------- helpers ----------

def _ensure_prefixed(msg: str) -> str:
    m = (msg or "").strip()
    if not m:
        return m
    return m if m.startswith(BOT_PREFIX) else f"{BOT_PREFIX} {m}"


def _pick_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        s = str(x).strip()
        if not s:
            return None
        return int(s)
    except Exception:
        return None


def _extract_media(payload: Dict[str, Any]) -> Tuple[str, Optional[int], Optional[int], Optional[str], Optional[str], Optional[int], Optional[int], Optional[int]]:
    """
    Return (media_type, tmdbId, tvdbId, imdbId, title, year, season, episode)
    Season/Episode best-effort: if not present, None.
    """
    media = payload.get("media") or {}
    issue = payload.get("issue") or {}
    req = payload.get("request") or {}
    comment = payload.get("comment") or {}

    media_d = media or issue.get("media") or req.get("media") or payload.get("subject") or {}

    tmdb = _pick_int(media_d.get("tmdbId") or issue.get("tmdbId") or media.get("tmdbId") or payload.get("tmdbId"))
    tvdb = _pick_int(media_d.get("tvdbId") or issue.get("tvdbId") or media.get("tvdbId") or payload.get("tvdbId"))
    imdb = (
        (media_d.get("imdbId") or media_d.get("imdb_id"))
        or (issue.get("imdbId") or issue.get("imdb_id"))
        or (media.get("imdbId") or media.get("imdb_id"))
        or payload.get("imdbId") or payload.get("imdb_id")
    )
    if isinstance(imdb, str) and imdb and not imdb.startswith("tt"):
        imdb = f"tt{imdb}"

    title = media_d.get("title") or media_d.get("name") or issue.get("title") or payload.get("title") or payload.get("name")
    year = _pick_int(media_d.get("year") or media_d.get("releaseYear") or payload.get("year") or payload.get("releaseYear"))

    raw_type = (media_d.get("mediaType") or media_d.get("type") or issue.get("mediaType") or payload.get("mediaType") or "").lower()
    if raw_type in ("show", "tv", "series"):
        media_type = "series"
    elif raw_type == "movie":
        media_type = "movie"
    else:
        media_type = "series" if tvdb else "movie"

    season = _pick_int(
        media_d.get("seasonNumber") or issue.get("season") or payload.get("season") or comment.get("season")
    )
    episode = _pick_int(
        media_d.get("episodeNumber") or issue.get("episode") or payload.get("episode") or comment.get("episode")
    )

    return media_type, tmdb, tvdb, imdb, title, year, season, episode


def _gather_text_for_keywords(payload: Dict[str, Any]) -> str:
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    title = (issue.get("title") or payload.get("title") or "").lower()
    desc = (issue.get("description") or "").lower()
    ctext = (comment.get("text") or comment.get("message") or "").lower()
    return " ".join([title, desc, ctext]).strip()


def _split_keywords(s: str) -> set[str]:
    return {kw.strip().lower() for kw in (s or "").split(",") if kw.strip()}


def _match_bucket(media_type: str, text: str) -> Optional[str]:
    """
    Return a logical bucket name if any keyword matches, else None.
    Buckets: audio, video, subtitle, wrong (movies only), other
    """
    t = text.lower()
    if media_type == "series":
        if any(kw in t for kw in _split_keywords(cfg.TV_AUDIO_KEYWORDS)): return "audio"
        if any(kw in t for kw in _split_keywords(cfg.TV_VIDEO_KEYWORDS)): return "video"
        if any(kw in t for kw in _split_keywords(cfg.TV_SUBTITLE_KEYWORDS)): return "subtitle"
        if any(kw in t for kw in _split_keywords(cfg.TV_OTHER_KEYWORDS)): return "other"
    else:
        if any(kw in t for kw in _split_keywords(cfg.MOVIE_AUDIO_KEYWORDS)): return "audio"
        if any(kw in t for kw in _split_keywords(cfg.MOVIE_VIDEO_KEYWORDS)): return "video"
        if any(kw in t for kw in _split_keywords(cfg.MOVIE_SUBTITLE_KEYWORDS)): return "subtitle"
        if any(kw in t for kw in _split_keywords(cfg.MOVIE_WRONG_KEYWORDS)): return "wrong"
        if any(kw in t for kw in _split_keywords(cfg.MOVIE_OTHER_KEYWORDS)): return "other"
    return None


async def _poll_until(predicate_coro, timeout_sec: int, interval_sec: int) -> bool:
    remaining = timeout_sec
    while remaining > 0:
        if await predicate_coro():
            return True
        await asyncio.sleep(interval_sec)
        remaining -= interval_sec
    return False


async def _already_posted_same(issue_id: Optional[int], intended_msg_prefixed: str) -> bool:
    """Best-effort de-dupe: if our last comment equals intended message, skip."""
    if not issue_id:
        return False
    try:
        issue = await jelly_fetch_issue(issue_id)
        if not isinstance(issue, dict):
            return False
        comments = issue.get("comments") or issue.get("activity") or []
        # comments may be newest-last or newest-first; just scan a few
        for c in (list(comments)[-5:] if isinstance(comments, list) else []):
            text = (c.get("message") or c.get("text") or "").strip()
            if text and text.strip() == intended_msg_prefixed.strip():
                return True
    except Exception:
        pass
    return False


# ---------- remediation flows ----------

async def _handle_series(issue_id: Optional[int], tvdb: Optional[int], imdb: Optional[str], title: Optional[str],
                         year: Optional[int], season: Optional[int], episode: Optional[int], bucket: Optional[str]) -> Dict[str, Any]:
    # Resolve series
    series = None
    if tvdb:
        series = await S.get_series_by_tvdb(tvdb)
    if not series and imdb:
        series = await S.get_series_by_imdb(imdb)
    if not series and title:
        series = await S.get_series_by_title(title)

    if not series:
        if issue_id:
            await jelly_comment(issue_id, _ensure_prefixed(
                f"Series not found in Sonarr. Title: {title or 'unknown'} "
                f"{f'({year})' if year else ''} {f'[tvdb:{tvdb}]' if tvdb else ''} {f'[imdb:{imdb}]' if imdb else ''}"
            ))
        return {"found": False, "acted": False}

    series_id = int(series["id"])

    # No keywords? Coach and exit (but only once).
    if not bucket:
        if issue_id:
            coach = cfg.MSG_KEYWORD_COACH or "Tip: include one of the auto-fix keywords next time so I can repair this automatically."
            coach_pref = _ensure_prefixed(coach)
            if not await _already_posted_same(issue_id, coach_pref):
                await jelly_comment(issue_id, coach_pref)
        return {"found": True, "acted": False, "seriesId": series_id}

    # Delete episode file(s)
    ep_ids = await S.find_episode_ids(series_id, season, episode)
    deleted = await S.delete_episodefiles_by_episode_ids(ep_ids)

    if issue_id:
        await jelly_comment(issue_id, _ensure_prefixed(
            f"Queued replacement: removed {deleted} episode file(s). Triggering search…"
        ))

    # Trigger search + verify
    await S.search_series(series_id)
    grabbed_pred = lambda: S.history_has_recent_grab(series_id, cfg.SONARR_VERIFY_GRAB_SEC)
    queue_pred = lambda: S.queue_has_series(series_id)
    ok = await _poll_until(grabbed_pred, cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC) or \
         await _poll_until(queue_pred, cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC)

    # Success comment
    if ok and issue_id:
        if season is not None and episode is not None:
            msg = cfg.MSG_TV_REPLACED_AND_GRABBED or "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue."
            msg = msg.format(title=title or series.get("title") or "Unknown", season=season or 0, episode=episode or 0)
        else:
            msg = cfg.MSG_TV_SEARCH_ONLY_GRABBED or "{title}: new downloads are being grabbed. Closing this issue."
            msg = msg.format(title=title or series.get("title") or "Unknown", season=0, episode=0)
        await jelly_comment(issue_id, _ensure_prefixed(msg))
        if cfg.CLOSE_JELLYSEERR_ISSUES:
            await jelly_close(issue_id)
        return {"found": True, "acted": True, "seriesId": series_id, "queued": True}

    # Couldn’t verify
    if issue_id:
        fail = cfg.MSG_AUTOCLOSE_FAIL or "Action completed but I couldn’t verify a new grab in time. Please keep an eye on it."
        await jelly_comment(issue_id, _ensure_prefixed(fail))
    return {"found": True, "acted": True, "seriesId": series_id, "queued": False}


async def _handle_movie(issue_id: Optional[int], tmdb: Optional[int], imdb: Optional[str], title: Optional[str],
                        year: Optional[int], bucket: Optional[str]) -> Dict[str, Any]:
    # Resolve movie
    movie = None
    if tmdb:
        movie = await R.get_movie_by_tmdb(tmdb)
    if not movie and imdb:
        movie = await R.get_movie_by_imdb(imdb)

    if not movie:
        if issue_id:
            await jelly_comment(issue_id, _ensure_prefixed(
                f"Movie not found in Radarr. Title: {title or 'unknown'} "
                f"{f'({year})' if year else ''} {f'[tmdb:{tmdb}]' if tmdb else ''} {f'[imdb:{imdb}]' if imdb else ''}"
            ))
        return {"found": False, "acted": False}

    movie_id = int(movie["id"])

    # No keywords? Coach and exit (but only once).
    if not bucket:
        if issue_id:
            coach = cfg.MSG_KEYWORD_COACH or "Tip: include one of the auto-fix keywords next time so I can repair this automatically."
            coach_pref = _ensure_prefixed(coach)
            if not await _already_posted_same(issue_id, coach_pref):
                await jelly_comment(issue_id, coach_pref)
        return {"found": True, "acted": False, "movieId": movie_id}

    # Delete movie files (keep library entry)
    deleted = await R.delete_moviefiles(movie_id)

    if issue_id:
        await jelly_comment(issue_id, _ensure_prefixed(
            f"Queued replacement: removed {deleted} movie file(s). Triggering search…"
        ))

    # Trigger search + verify
    await R.search_movie(movie_id)
    grabbed_pred = lambda: R.history_has_recent_grab(movie_id, cfg.RADARR_VERIFY_GRAB_SEC)
    queue_pred = lambda: R.queue_has_movie(movie_id)
    ok = await _poll_until(grabbed_pred, cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC) or \
         await _poll_until(queue_pred, cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        msg = cfg.MSG_MOVIE_REPLACED_AND_GRABBED or "{title}: replaced file; new download grabbed. Closing this issue."
        msg = msg.format(title=title or movie.get("title") or "Unknown")
        await jelly_comment(issue_id, _ensure_prefixed(msg))
        if cfg.CLOSE_JELLYSEERR_ISSUES:
            await jelly_close(issue_id)
        return {"found": True, "acted": True, "movieId": movie_id, "queued": True}

    if issue_id:
        fail = cfg.MSG_AUTOCLOSE_FAIL or "Action completed but I couldn’t verify a new grab in time. Please keep an eye on it."
        await jelly_comment(issue_id, _ensure_prefixed(fail))
    return {"found": True, "acted": True, "movieId": movie_id, "queued": False}


# ---------- public handler ----------

async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # We ignore our own comments regardless of the event name/string.
    comment = payload.get("comment") or {}
    comment_text = (comment.get("text") or comment.get("message") or "").strip()
    if comment_text and is_our_comment(comment_text):
        return {"ok": True, "ignored": "own_comment"}

    event = (payload.get("event") or "").lower()
    issue = payload.get("issue") or {}
    issue_id = _pick_int(issue.get("issue_id") or issue.get("id"))

    media_type, tmdb, tvdb, imdb, title, year, season, episode = _extract_media(payload)
    text = _gather_text_for_keywords(payload)
    bucket = _match_bucket(media_type, text)

    log.info(
        "Webhook event=%s issue_id=%s media_type=%s tmdb=%s tvdb=%s imdb=%s title=%s year=%s season=%s episode=%s bucket=%s",
        event or "unknown", issue_id, media_type, tmdb, tvdb, imdb, title, year, season, episode, bucket
    )

    # Optional human ack on user comments (but only if it's not ours)
    if event.find("comment") != -1 and comment_text and not is_our_comment(comment_text) and cfg.ACK_ON_COMMENT_CREATED and issue_id:
        await jelly_comment(issue_id, _ensure_prefixed("Thanks! Running automated remediation…"))

    # Act based on media type
    if media_type == "series":
        detail = await _handle_series(issue_id, tvdb, imdb, title, year, season, episode, bucket)
    else:
        detail = await _handle_movie(issue_id, tmdb, imdb, title, year, bucket)

    return {"ok": True, "event": event or "unknown", "issue_id": issue_id, "detail": detail}
