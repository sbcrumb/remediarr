from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, Optional, Tuple

from app.config import cfg
from app.logging import log
from app.services import radarr as R
from app.services import sonarr as S
from app.services.jellyseerr import (
    jelly_comment,
    jelly_close,
    jelly_fetch_issue,
    is_our_comment,
    BOT_PREFIX,  # hardcoded “[Remediarr]”
)

# -----------------------------------------------------------------------------
# Cooldown (issue_id -> unix_until)
# -----------------------------------------------------------------------------
_COOLDOWN: Dict[int, float] = {}


def _cooldown_secs() -> int:
    try:
        return int(getattr(cfg, "REMEDIARR_ISSUE_COOLDOWN_SEC", 90))
    except Exception:
        return 90


def _under_cooldown(issue_id: Optional[int]) -> Tuple[bool, int]:
    if not issue_id:
        return False, 0
    now = int(time.time())
    until = int(_COOLDOWN.get(int(issue_id), 0))
    if now < until:
        return True, (until - now)
    return False, 0


def _arm_cooldown(issue_id: Optional[int]) -> None:
    if issue_id:
        _COOLDOWN[int(issue_id)] = int(time.time()) + _cooldown_secs()


def _close_issues_enabled() -> bool:
    if hasattr(cfg, "CLOSE_JELLYSEERR_ISSUES"):
        return bool(cfg.CLOSE_JELLYSEERR_ISSUES)
    return bool(getattr(cfg, "JELLYSEERR_CLOSE_ISSUES", True))


def _ensure_prefixed(msg: str) -> str:
    prefix = BOT_PREFIX or "[Remediarr]"
    return msg if msg.strip().startswith(prefix) else f"{prefix} {msg}"


async def _poll_until(pred_coro_factory, total_sec: int, poll_sec: int) -> bool:
    total = max(0, int(total_sec or 0))
    step = max(1, int(poll_sec or 1))
    deadline = time.time() + total
    while time.time() <= deadline:
        ok = await pred_coro_factory()
        if ok:
            return True
        await asyncio.sleep(step)
    return False


# -----------------------------------------------------------------------------
# Payload helpers
# -----------------------------------------------------------------------------
_SXE_RE = re.compile(r"[Ss](\d+)[Ee](\d+)")


def _deep_iter(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _deep_iter(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _deep_iter(v)


def _to_int(val: Any) -> Optional[int]:
    try:
        if val is None:
            return None
        if isinstance(val, bool):
            return None
        s = str(val).strip()
        if not s:
            return None
        return int(s)
    except Exception:
        return None


def _extract_issue_id(payload: Dict[str, Any]) -> Optional[int]:
    # Exact template you provided: issue.issue_id
    issue = payload.get("issue") or {}
    iid = _to_int(issue.get("issue_id"))
    if iid is not None:
        return iid

    # Other possibilities we’ve seen
    iid = _to_int(issue.get("id"))
    if iid is not None:
        return iid

    # Generic fallbacks anywhere
    for node in _deep_iter(payload):
        if not isinstance(node, dict):
            continue
        if "issueId" in node:
            iid = _to_int(node.get("issueId"))
            if iid is not None:
                return iid
        if "issue_id" in node:
            iid = _to_int(node.get("issue_id"))
            if iid is not None:
                return iid
        if "id" in node and (node.get("type") == "issue" or node.get("subject") == "issue"):
            iid = _to_int(node.get("id"))
            if iid is not None:
                return iid
    return None


def _extract_media_type(payload: Dict[str, Any]) -> Optional[str]:
    # Exact template: media.media_type
    media = payload.get("media") or {}
    mt = media.get("media_type")
    if isinstance(mt, str) and mt.strip():
        return mt.lower()

    # Fallbacks
    mt = media.get("mediaType") or payload.get("media_type") or payload.get("mediaType")
    if isinstance(mt, str) and mt.strip():
        return mt.lower()

    # Deep fallback
    for node in _deep_iter(payload):
        if not isinstance(node, dict):
            continue
        m = node.get("media")
        if isinstance(m, dict):
            mt = m.get("media_type") or m.get("mediaType")
            if isinstance(mt, str) and mt.strip():
                return mt.lower()
    return None


def _parse_sxe_from_string(s: str) -> Tuple[Optional[int], Optional[int]]:
    m = _SXE_RE.search(s or "")
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None, None


def _extract_season_episode(doc: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    # Your template: issue.affected_season / issue.affected_episode
    issue = (doc or {}).get("issue") or {}
    s = _to_int(issue.get("affected_season"))
    e = _to_int(issue.get("affected_episode"))
    if s is not None and e is not None:
        return s, e

    # Common alternates
    for node in _deep_iter(doc):
        if not isinstance(node, dict):
            continue
        s = _to_int(node.get("season")) or _to_int(node.get("seasonNumber"))
        e = _to_int(node.get("episode")) or _to_int(node.get("episodeNumber"))
        if s is not None and e is not None:
            return s, e

    # SxxExx encoded strings
    for node in _deep_iter(doc):
        if not isinstance(node, dict):
            continue
        for k in ("sxe", "episodeCode", "code"):
            val = node.get(k)
            if isinstance(val, str):
                ss, ee = _parse_sxe_from_string(val)
                if ss is not None and ee is not None:
                    return ss, ee

    return None, None


def _last_human_comment_text(issue_json: Optional[Dict[str, Any]]) -> Optional[str]:
    if not issue_json:
        return None
    comments = issue_json.get("comments") or []
    if not isinstance(comments, list):
        return None
    for c in reversed(comments):
        msg = (c or {}).get("message") or ""
        if not is_our_comment(msg):
            return msg
    return None


def _classify_bucket(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip().lower()

    def any_in(csv: str) -> bool:
        words = [w.strip().lower() for w in (csv or "").split(",") if w.strip()]
        return any(w in t for w in words)

    if any_in(cfg.MOVIE_VIDEO_KEYWORDS) or any_in(cfg.TV_VIDEO_KEYWORDS):
        return "video"
    if any_in(cfg.MOVIE_AUDIO_KEYWORDS) or any_in(cfg.TV_AUDIO_KEYWORDS):
        return "audio"
    if any_in(cfg.MOVIE_SUBTITLE_KEYWORDS) or any_in(cfg.TV_SUBTITLE_KEYWORDS):
        return "subtitle"
    if any_in(cfg.MOVIE_WRONG_KEYWORDS):
        return "wrong"
    if any_in(cfg.MOVIE_OTHER_KEYWORDS) or any_in(cfg.TV_OTHER_KEYWORDS):
        return "other"
    return None


def _extract_ids(payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    # Respect your template first
    media = payload.get("media") or {}
    tmdb = _to_int(media.get("tmdbId"))
    tvdb = _to_int(media.get("tvdbId"))
    imdb = None  # not present in your template

    # Fall back to other shapes if missing
    if tmdb is None or tvdb is None:
        for node in _deep_iter(payload):
            if not isinstance(node, dict):
                continue
            m = node.get("media")
            if isinstance(m, dict):
                tmdb = tmdb if tmdb is not None else _to_int(m.get("tmdbId"))
                tvdb = tvdb if tvdb is not None else _to_int(m.get("tvdbId"))
                imdb = imdb or m.get("imdbId")
    return tmdb, tvdb, imdb


def _extract_comment_text(payload: Dict[str, Any]) -> Optional[str]:
    comment = payload.get("comment") or {}
    text = comment.get("comment_message") or comment.get("message") or comment.get("text")
    if isinstance(text, str) and text.strip():
        return text
    # fallback: sometimes the top-level "message" contains the human text
    top = payload.get("message")
    if isinstance(top, str) and top.strip():
        return top
    return None


# -----------------------------------------------------------------------------
# Main entry
# -----------------------------------------------------------------------------
async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    event = (payload.get("event") or payload.get("type") or "").lower()

    issue_id = _extract_issue_id(payload)
    media_type = _extract_media_type(payload) or ""
    tmdb, tvdb, imdb = _extract_ids(payload)

    # Fetch full issue (source of truth) if we have an id
    issue_json = None
    if issue_id:
        try:
            issue_json = await jelly_fetch_issue(issue_id)
        except Exception as e:
            log.info("Jellyseerr: fetch issue %s failed: %s", issue_id, e)

    # Prefer media type from the fetched issue if present
    if issue_json:
        media_type = (issue_json.get("mediaType") or media_type or "").lower()

    # Determine bucket from the latest human comment
    comment_text = _extract_comment_text(payload)
    if issue_json and not comment_text:
        comment_text = _last_human_comment_text(issue_json)
    if comment_text:
        log.info("Jellyseerr: last comment on issue %s: %r", issue_id, comment_text)
    bucket = _classify_bucket(comment_text)
    if comment_text:
        log.info("Keyword scan: %r -> bucket=%s", comment_text, bucket)

    # Season/Episode for series
    season, episode = _extract_season_episode(issue_json or payload)

    log.info(
        "Webhook event=%s issue_id=%s media_type=%s tmdb=%s tvdb=%s imdb=%s season=%s episode=%s bucket=%s",
        event or "?", issue_id, media_type or "?", tmdb, tvdb, imdb, season, episode, bucket,
    )

    # Loop prevention
    cd, remain = _under_cooldown(issue_id)
    if cd:
        log.info("Issue %s under cooldown (%ss remaining) — skipping.", issue_id, remain)
        return {"cooldown": True}

    # Coach if no keywords
    if not bucket:
        tip = (
            "Tip: include one of these keywords next time so I can repair this automatically: "
            "audio: no audio, no sound, audio issue, wrong language, not in english | "
            "video: no video, video missing, bad video, broken video, black screen | "
            "subtitle: missing subs, no subtitles, bad subtitles, wrong subs, subs out of sync"
        )
        if comment_text and not is_our_comment(comment_text) and issue_id:
            msg = _ensure_prefixed(tip)
            log.info("Outgoing coaching comment: %r", msg)
            await jelly_comment(issue_id, msg, force_prefix=False)
            _arm_cooldown(issue_id)
        return {"coached": True}

    # Movies
    if (media_type or "").lower() == "movie":
        detail = await _handle_movie(issue_id, tmdb, imdb)
        _arm_cooldown(issue_id)
        return detail

    # Series (episode-only)
    if (media_type or "").lower() in ("tv", "show", "series"):
        if season is None or episode is None:
            log.info("Series missing season/episode. Not acting to avoid season-wide search.")
            return {"series": True, "skipped": "no-season-episode"}
        detail = await _handle_series(issue_id, tvdb, season, episode)
        _arm_cooldown(issue_id)
        return detail

    log.info("Unknown or missing media_type; ignoring.")
    return {"ignored": True}


# -----------------------------------------------------------------------------
# Movie path
# -----------------------------------------------------------------------------
async def _handle_movie(issue_id: Optional[int], tmdb: Optional[int], imdb: Optional[str]) -> Dict[str, Any]:
    if not tmdb and not imdb:
        return {"movie": True, "skipped": "no-ids"}

    movie = await (R.get_movie_by_tmdb(tmdb) if tmdb else R.get_movie_by_imdb(imdb))
    if not movie:
        return {"movie": True, "skipped": "not-in-radarr"}

    movie_id = int(movie["id"])
    title = movie.get("title") or "This title"

    deleted = await R.delete_moviefiles(movie_id)
    await R.search_movie(movie_id)

    ok = await _poll_until(lambda: R.queue_has_movie(movie_id), cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC) \
         or await _poll_until(lambda: R.history_has_recent_grab(movie_id, cfg.RADARR_VERIFY_GRAB_SEC), cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        tmpl = (cfg.MSG_MOVIE_REPLACED_AND_GRABBED if deleted else cfg.MSG_MOVIE_SEARCH_ONLY_GRABBED) \
               or "{title}: new download grabbed. Closing this issue."
        msg = _ensure_prefixed(tmpl.format(title=title, deleted=deleted))
        log.info("Outgoing comment: %r", msg)
        await jelly_comment(issue_id, msg, force_prefix=False)
        if _close_issues_enabled():
            try:
                await jelly_close(issue_id, silent=True)
            except Exception as e:
                log.info("Close attempt failed but continuing (issue %s): %s", issue_id, e)
        return {"movie": True, "queued": True, "deleted": deleted, "closed": True}

    return {"movie": True, "queued": True, "deleted": deleted, "closed": False}


# -----------------------------------------------------------------------------
# Series path (episode-only search)
# -----------------------------------------------------------------------------
async def _handle_series(issue_id: Optional[int], tvdb: Optional[int], season: int, episode: int) -> Dict[str, Any]:
    if not tvdb:
        return {"series": True, "skipped": "no-tvdb"}

    series = await S.get_series_by_tvdb(tvdb)
    if not series:
        return {"series": True, "skipped": "not-in-sonarr"}

    series_id = int(series["id"])
    title = series.get("title") or "This show"

    ep_ids = await S.find_episode_ids(series_id, season, episode)
    if not ep_ids:
        log.info("Sonarr: could not map S%02dE%02d to episode ids; not acting.", season, episode)
        return {"series": True, "skipped": "no-episode-ids"}

    deleted = await S.delete_episodefiles_by_episode_ids(series_id, ep_ids)
    await S.search_episode_ids(ep_ids)

    ok = await _poll_until(lambda: S.queue_has_any_of_episode_ids(ep_ids), cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        tmpl = (cfg.MSG_TV_REPLACED_AND_GRABBED if deleted else cfg.MSG_TV_SEARCH_ONLY_GRABBED) \
               or "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue."
        msg = _ensure_prefixed(tmpl.format(title=title, season=season, episode=episode, deleted=deleted))
        log.info("Outgoing comment: %r", msg)
        await jelly_comment(issue_id, msg, force_prefix=False)
        if _close_issues_enabled():
            try:
                await jelly_close(issue_id, silent=True)
            except Exception as e:
                log.info("Close attempt failed but continuing (issue %s): %s", issue_id, e)
        return {"series": True, "queued": True, "deleted": deleted, "closed": True}

    return {"series": True, "queued": True, "deleted": deleted, "closed": False}
