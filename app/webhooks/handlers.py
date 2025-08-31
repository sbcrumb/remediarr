from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple, Iterable, OrderedDict

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

    season = _pick_int(media_d.get("seasonNumber") or issue.get("season") or payload.get("season") or comment.get("season"))
    episode = _pick_int(media_d.get("episodeNumber") or issue.get("episode") or payload.get("episode") or comment.get("episode"))

    return media_type, tmdb, tvdb, imdb, title, year, season, episode


def _gather_text_for_keywords(payload: Dict[str, Any]) -> str:
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    title = (issue.get("title") or payload.get("title") or "").lower()
    desc = (issue.get("description") or "").lower()
    ctext = (comment.get("text") or comment.get("message") or "").lower()
    return " ".join([title, desc, ctext]).strip()


def _split_keywords(s: str) -> list[str]:
    return [kw.strip().lower() for kw in (s or "").split(",") if kw.strip()]


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _keyword_buckets_for_media(media_type: str) -> "OrderedDict[str, list[str]]":
    from collections import OrderedDict as OD
    if media_type == "series":
        buckets = OD()
        buckets["audio"] = _split_keywords(cfg.TV_AUDIO_KEYWORDS)
        buckets["video"] = _split_keywords(cfg.TV_VIDEO_KEYWORDS)
        buckets["subtitle"] = _split_keywords(cfg.TV_SUBTITLE_KEYWORDS)
        buckets["other"] = _split_keywords(cfg.TV_OTHER_KEYWORDS)
    else:
        buckets = OD()
        buckets["audio"] = _split_keywords(cfg.MOVIE_AUDIO_KEYWORDS)
        buckets["video"] = _split_keywords(cfg.MOVIE_VIDEO_KEYWORDS)
        buckets["subtitle"] = _split_keywords(cfg.MOVIE_SUBTITLE_KEYWORDS)
        buckets["wrong"] = _split_keywords(cfg.MOVIE_WRONG_KEYWORDS)
        buckets["other"] = _split_keywords(cfg.MOVIE_OTHER_KEYWORDS)
    for k in list(buckets.keys()):
        buckets[k] = _dedupe_preserve_order(buckets[k])
    return buckets


def _keywords_text_grouped(media_type: str) -> str:
    buckets = _keyword_buckets_for_media(media_type)
    parts = []
    for name, kws in buckets.items():
        if kws:
            parts.append(f"{name}: {', '.join(kws)}")
    return " | ".join(parts) if parts else "no keywords configured"


def _match_bucket(media_type: str, text: str) -> Optional[str]:
    """
    Return a logical bucket name if any keyword matches, else None.
    Buckets: audio, video, subtitle, wrong (movies only), other
    """
    t = (text or "").lower()
    if not t:
        return None
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


def _latest_human_comment_text(issue_json: Dict[str, Any] | None) -> Optional[str]:
    """Return the most recent non-bot comment text from the issue JSON."""
    if not issue_json:
        return None
    comments = issue_json.get("comments") or issue_json.get("activity") or []
    if not isinstance(comments, list):
        return None
    for c in reversed(comments):
        txt = (c.get("message") or c.get("text") or "").strip()
        if txt and not is_our_comment(txt):
            return txt
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
    series = None
    if tvdb:
        series = await S.get_series_by_tvdb(tvdb)
    if not series and imdb:
        series = await S.get_series_by_imdb(imdb)
    if not series and title:
        series = await S.get_series_by_title(title)

    if not series:
        if issue_id:
            msg = _ensure_prefixed(
                f"Series not found in Sonarr. Title: {title or 'unknown'} "
                f"{f'({year})' if year else ''} {f'[tvdb:{tvdb}]' if tvdb else ''} {f'[imdb:{imdb}]' if imdb else ''}"
            )
            log.info("Outgoing comment (not found): %r", msg)
            await jelly_comment(issue_id, msg, force_prefix=False)
        return {"found": False, "acted": False}

    series_id = int(series["id"])
    kws_text = _keywords_text_grouped("series")

    if not bucket:
        if issue_id:
            template = cfg.MSG_KEYWORD_COACH or "Tip: include one of these keywords next time so I can repair this automatically: {keywords}."
            coach = template.format(
                title=title or series.get("title") or "Unknown",
                season=season or 0,
                episode=episode or 0,
                keywords=kws_text,
            )
            coach_pref = _ensure_prefixed(coach)
            if not await _already_posted_same(issue_id, coach_pref):
                log.info("Outgoing coaching comment: %r", coach_pref[:300])
                await jelly_comment(issue_id, coach_pref, force_prefix=False)
        return {"found": True, "acted": False, "seriesId": series_id}

    ep_ids = await S.find_episode_ids(series_id, season, episode)
    deleted = await S.delete_episodefiles_by_episode_ids(ep_ids)
    if issue_id:
        msg = _ensure_prefixed(f"Queued replacement: removed {deleted} episode file(s). Triggering search…")
        log.info("Outgoing comment: %r", msg)
        await jelly_comment(issue_id, msg, force_prefix=False)

    await S.search_series(series_id)
    grabbed_pred = lambda: S.history_has_recent_grab(series_id, cfg.SONARR_VERIFY_GRAB_SEC)
    queue_pred = lambda: S.queue_has_series(series_id)
    ok = await _poll_until(grabbed_pred, cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC) or \
         await _poll_until(queue_pred, cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        if season is not None and episode is not None:
            msg = (cfg.MSG_TV_REPLACED_AND_GRABBED or
                   "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue.")
            msg = msg.format(
                title=title or series.get("title") or "Unknown",
                season=season or 0,
                episode=episode or 0,
                keywords=kws_text,
            )
        else:
            msg = (cfg.MSG_TV_SEARCH_ONLY_GRABBED or
                   "{title}: new downloads are being grabbed. Closing this issue.")
            msg = msg.format(
                title=title or series.get("title") or "Unknown",
                season=0,
                episode=0,
                keywords=kws_text,
            )
        msg = _ensure_prefixed(msg)
        log.info("Outgoing success comment: %r", msg[:300])
        await jelly_comment(issue_id, msg, force_prefix=False)
        if cfg.CLOSE_JELLYSEERR_ISSUES:
            await jelly_close(issue_id)
        return {"found": True, "acted": True, "seriesId": series_id, "queued": True}

    if issue_id:
        fail = (cfg.MSG_AUTOCLOSE_FAIL or
                "Action completed but I couldn’t verify a new grab in time. Please keep an eye on it.")
        msg = _ensure_prefixed(fail.format(
            title=title or series.get("title") or "Unknown",
            season=season or 0,
            episode=episode or 0,
            keywords=kws_text,
        ))
        log.info("Outgoing fail comment: %r", msg[:300])
        await jelly_comment(issue_id, msg, force_prefix=False)
    return {"found": True, "acted": True, "seriesId": series_id, "queued": False}


async def _handle_movie(issue_id: Optional[int], tmdb: Optional[int], imdb: Optional[str], title: Optional[str],
                        year: Optional[int], bucket: Optional[str]) -> Dict[str, Any]:
    movie = None
    if tmdb:
        movie = await R.get_movie_by_tmdb(tmdb)
    if not movie and imdb:
        movie = await R.get_movie_by_imdb(imdb)

    if not movie:
        if issue_id:
            msg = _ensure_prefixed(
                f"Movie not found in Radarr. Title: {title or 'unknown'} "
                f"{f'({year})' if year else ''} {f'[tmdb:{tmdb}]' if tmdb else ''} {f'[imdb:{imdb}]' if imdb else ''}"
            )
            log.info("Outgoing comment (not found): %r", msg)
            await jelly_comment(issue_id, msg, force_prefix=False)
        return {"found": False, "acted": False}

    movie_id = int(movie["id"])
    kws_text = _keywords_text_grouped("movie")

    if not bucket:
        if issue_id:
            template = cfg.MSG_KEYWORD_COACH or "Tip: include one of these keywords next time so I can repair this automatically: {keywords}."
            coach = template.format(
                title=title or movie.get("title") or "Unknown",
                season=0,
                episode=0,
                keywords=kws_text,
            )
            coach_pref = _ensure_prefixed(coach)
            if not await _already_posted_same(issue_id, coach_pref):
                log.info("Outgoing coaching comment: %r", coach_pref[:300])
                await jelly_comment(issue_id, coach_pref, force_prefix=False)
        return {"found": True, "acted": False, "movieId": movie_id}

    deleted = await R.delete_moviefiles(movie_id)
    if issue_id:
        msg = _ensure_prefixed(f"Queued replacement: removed {deleted} movie file(s). Triggering search…")
        log.info("Outgoing comment: %r", msg)
        await jelly_comment(issue_id, msg, force_prefix=False)

    await R.search_movie(movie_id)
    grabbed_pred = lambda: R.history_has_recent_grab(movie_id, cfg.RADARR_VERIFY_GRAB_SEC)
    queue_pred = lambda: R.queue_has_movie(movie_id)
    ok = await _poll_until(grabbed_pred, cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC) or \
         await _poll_until(queue_pred, cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        msg = (cfg.MSG_MOVIE_REPLACED_AND_GRABBED or
               "{title}: replaced file; new download grabbed. Closing this issue.")
        msg = msg.format(
            title=title or movie.get("title") or "Unknown",
            season=0,
            episode=0,
            keywords=kws_text,
        )
        msg = _ensure_prefixed(msg)
        log.info("Outgoing success comment: %r", msg[:300])
        await jelly_comment(issue_id, msg, force_prefix=False)
        if cfg.CLOSE_JELLYSEERR_ISSUES:
            await jelly_close(issue_id)
        return {"found": True, "acted": True, "movieId": movie_id, "queued": True}

    if issue_id:
        fail = (cfg.MSG_AUTOCLOSE_FAIL or
                "Action completed but I couldn’t verify a new grab in time. Please keep an eye on it.")
        msg = _ensure_prefixed(fail.format(
            title=title or movie.get("title") or "Unknown",
            season=0,
            episode=0,
            keywords=kws_text,
        ))
        log.info("Outgoing fail comment: %r", msg[:300])
        await jelly_comment(issue_id, msg, force_prefix=False)
    return {"found": True, "acted": True, "movieId": movie_id, "queued": False}


# ---------- public handler ----------

async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Inbound comment (for logging + self-loop guard)
    comment = payload.get("comment") or {}
    comment_text = (comment.get("text") or comment.get("message") or "").strip()
    if comment_text:
        log.info("Inbound comment text (payload): %r", comment_text.replace("\n", " ")[:300])
        if is_our_comment(comment_text):
            log.info("Ignoring own comment.")
            return {"ok": True, "ignored": "own_comment"}

    event = (payload.get("event") or "").lower()
    issue = payload.get("issue") or {}
    issue_id = _pick_int(issue.get("issue_id") or issue.get("id"))

    media_type, tmdb, tvdb, imdb, title, year, season, episode = _extract_media(payload)
    text = _gather_text_for_keywords(payload)
    bucket = _match_bucket(media_type, text)

    # Fallback: if no payload comment or no bucket match, fetch the issue and scan the latest human comment
    if (not comment_text or not text) or bucket is None:
        issue_json = await jelly_fetch_issue(issue_id) if issue_id else None
        last_human = _latest_human_comment_text(issue_json)
        if last_human:
            combined = (text + " " + last_human).strip() if text else last_human
            bucket2 = _match_bucket(media_type, combined)
            log.info("Keyword scan (fallback last human comment): %r -> bucket=%s", last_human[:200], bucket2)
            if bucket2:
                text = combined
                bucket = bucket2

    log.info(
        "Webhook event=%s issue_id=%s media_type=%s tmdb=%s tvdb=%s imdb=%s title=%s year=%s season=%s episode=%s bucket=%s",
        event or "unknown", issue_id, media_type, tmdb, tvdb, imdb, title, year, season, episode, bucket
    )
    if text:
        log.debug("Keyword scan text preview: %r", text[:300])

    # Optional ack on user comments (not ours)
    if event.find("comment") != -1 and comment_text and not is_our_comment(comment_text) and cfg.ACK_ON_COMMENT_CREATED and issue_id:
        msg = _ensure_prefixed("Thanks! Running automated remediation…")
        log.info("Outgoing ack comment: %r", msg)
        await jelly_comment(issue_id, msg, force_prefix=False)

    if media_type == "series":
        detail = await _handle_series(issue_id, tvdb, imdb, title, year, season, episode, bucket)
    else:
        detail = await _handle_movie(issue_id, tmdb, imdb, title, year, bucket)

    return {"ok": True, "event": event or "unknown", "issue_id": issue_id, "detail": detail}
