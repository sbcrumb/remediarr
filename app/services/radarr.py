from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, Optional, Sequence, Tuple

from app.config import cfg, BOT_PREFIX
from app.logging import log
from app.services.jellyseerr import (
    jelly_comment,
    jelly_close,
    jelly_fetch_issue,
    is_our_comment,
)
from app.services import radarr as R
from app.services import sonarr as S

# -------------------------------------------------------------------
# Cooldown
# -------------------------------------------------------------------

_COOLDOWN: Dict[int, float] = {}  # issue_id -> unix_until


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
    if not issue_id:
        return
    _COOLDOWN[int(issue_id)] = int(time.time()) + _cooldown_secs()


def _close_issues_enabled() -> bool:
    # Back-compat with older env names if present
    if hasattr(cfg, "CLOSE_JELLYSEERR_ISSUES"):
        return bool(cfg.CLOSE_JELLYSEERR_ISSUES)
    return bool(getattr(cfg, "JELLYSEERR_CLOSE_ISSUES", True))


def _ensure_prefixed(msg: str) -> str:
    prefix = BOT_PREFIX or "[Remediarr]"
    return msg if msg.strip().startswith(prefix) else f"{prefix} {msg}"


async def _poll_until(pred_coro_factory, total_sec: int, poll_sec: int) -> bool:
    total = max(0, int(total_sec))
    step = max(1, int(poll_sec))
    deadline = time.time() + total
    while time.time() <= deadline:
        ok = await pred_coro_factory()
        if ok:
            return True
        await asyncio.sleep(step)
    return False


# -------------------------------------------------------------------
# Payload digging
# -------------------------------------------------------------------

_SXE_RE = re.compile(r"[Ss](\d+)[Ee](\d+)")


def _deep_iter(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _deep_iter(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _deep_iter(v)


def _extract_issue_id(payload: Dict[str, Any]) -> Optional[int]:
    """
    New Jellyseerr payloads may look like:
      { subject: { type: "issue", id: 42 }, ... }
    or nested as data.issue.id, or issueId.
    We ONLY accept ids tied to an 'issue' context to avoid grabbing random 'id'.
    """
    # subject.type == issue
    subj = payload.get("subject")
    if isinstance(subj, dict) and subj.get("type") == "issue" and "id" in subj:
        try:
            return int(subj["id"])
        except Exception:
            pass

    # nested ... issue: { id: ... }
    for node in _deep_iter(payload):
        if isinstance(node, dict) and "issue" in node and isinstance(node["issue"], dict):
            iid = node["issue"].get("id")
            try:
                if iid is not None:
                    return int(iid)
            except Exception:
                pass

    # explicit issueId anywhere
    for node in _deep_iter(payload):
        if isinstance(node, dict) and "issueId" in node:
            try:
                return int(node["issueId"])
            except Exception:
                pass

    return None


def _extract_media_type(payload: Dict[str, Any]) -> Optional[str]:
    # best-effort; we'll also rely on fetched issue below
    for node in _deep_iter(payload):
        if isinstance(node, dict):
            mt = node.get("mediaType")
            if isinstance(mt, str) and mt.strip():
                return mt.lower()
            media = node.get("media")
            if isinstance(media, dict):
                mt = media.get("mediaType")
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


def _extract_season_episode(payload_or_issue: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    # direct numeric fields
    for node in _deep_iter(payload_or_issue):
        if not isinstance(node, dict):
            continue
        s = node.get("affectedSeason") or node.get("season") or node.get("seasonNumber")
        e = node.get("affectedEpisode") or node.get("episode") or node.get("episodeNumber")
        try:
            if s is not None and e is not None:
                return int(s), int(e)
        except Exception:
            pass

    # encoded "S07E13"
    for node in _deep_iter(payload_or_issue):
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


# -------------------------------------------------------------------
# Main entry
# -------------------------------------------------------------------

async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    event = (payload.get("event") or payload.get("type") or "").lower()

    issue_id = _extract_issue_id(payload)
    media_type = _extract_media_type(payload) or ""
    tmdb = None
    tvdb = None
    imdb = None

    issue_json = None
    if issue_id:
        try:
            issue_json = await jelly_fetch_issue(issue_id)
        except Exception as e:
            log.info("Jellyseerr: fetch issue %s failed: %s", issue_id, e)

    if issue_json:
        media_type = (issue_json.get("mediaType") or media_type or "").lower()
        # IDs
        tmdb = issue_json.get("tmdbId") or (issue_json.get("media") or {}).get("tmdbId")
        tvdb = issue_json.get("tvdbId") or (issue_json.get("media") or {}).get("tvdbId")
        imdb = issue_json.get("imdbId") or (issue_json.get("media") or {}).get("imdbId")

    # Try fallbacks from raw payload if still missing
    if tmdb is None or tvdb is None or imdb is None:
        for node in _deep_iter(payload):
            if isinstance(node, dict):
                media = node.get("media")
                if isinstance(media, dict):
                    tmdb = tmdb or media.get("tmdbId")
                    tvdb = tvdb or media.get("tvdbId")
                    imdb = imdb or media.get("imdbId")

    # Comment text for bucket classification
    comment_text = (payload.get("comment") or {}).get("message") \
                   or (payload.get("comment") or {}).get("text")
    if issue_json and not comment_text:
        comment_text = _last_human_comment_text(issue_json)
    bucket = _classify_bucket(comment_text)

    # S/E for series
    season, episode = _extract_season_episode(issue_json or payload)

    log.info(
        "Webhook event=%s issue_id=%s media_type=%s tmdb=%s tvdb=%s imdb=%s season=%s episode=%s bucket=%s",
        event or "?", issue_id, media_type or "?", tmdb, tvdb, imdb, season, episode, bucket
    )

    # loop prevention
    cd, remain = _under_cooldown(issue_id)
    if cd:
        log.info("Issue %s under cooldown (%ss remaining) — skipping.", issue_id, remain)
        return {"cooldown": True}

    # Coaching if no keywords
    if not bucket:
        tip = (
            "Tip: include one of these keywords next time so I can repair this automatically: "
            "audio: no audio, no sound, audio issue, wrong language, not in english | "
            "video: no video, video missing, bad video, broken video, black screen | "
            "subtitle: missing subs, no subtitles, bad subtitles, wrong subs, subs out of sync"
        )
        if comment_text and not is_our_comment(comment_text) and issue_id:
            await jelly_comment(issue_id, _ensure_prefixed(tip), force_prefix=False)
            _arm_cooldown(issue_id)
        return {"coached": True}

    # --- Movies ---
    if (media_type or "").lower() == "movie":
        detail = await _handle_movie(issue_id, tmdb, imdb)
        _arm_cooldown(issue_id)
        return detail

    # --- Series ---
    if (media_type or "").lower() in ("tv", "show", "series"):
        if season is None or episode is None:
            log.info("Series missing season/episode. Not acting to avoid season-wide search.")
            return {"series": True, "skipped": "no-season-episode"}
        detail = await _handle_series(issue_id, tvdb, season, episode)
        _arm_cooldown(issue_id)
        return detail

    log.info("Unknown or missing media_type; ignoring.")
    return {"ignored": True}


# -------------------------------------------------------------------
# Movie path
# -------------------------------------------------------------------

async def _handle_movie(issue_id: Optional[int], tmdb: Optional[int], imdb: Optional[str]) -> Dict[str, Any]:
    if not tmdb and not imdb:
        return {"movie": True, "skipped": "no-ids"}

    movie = await R.get_movie_by_tmdb(tmdb) if tmdb else await R.get_movie_by_imdb(imdb)
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
        await jelly_comment(issue_id, msg, force_prefix=False)
        if _close_issues_enabled():
            try:
                await jelly_close(issue_id, silent=True)
            except Exception as e:
                log.info("Close attempt failed but continuing (issue %s): %s", issue_id, e)
        return {"movie": True, "queued": True, "deleted": deleted, "closed": True}

    return {"movie": True, "queued": True, "deleted": deleted, "closed": False}


# -------------------------------------------------------------------
# Series path (episode-only search)
# -------------------------------------------------------------------

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
    # (We purposely do not rely on Sonarr history here because some deployments 400 that endpoint.)

    if ok and issue_id:
        tmpl = (cfg.MSG_TV_REPLACED_AND_GRABBED if deleted else cfg.MSG_TV_SEARCH_ONLY_GRABBED) \
               or "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue."
        msg = _ensure_prefixed(tmpl.format(title=title, season=season, episode=episode, deleted=deleted))
        await jelly_comment(issue_id, msg, force_prefix=False)
        if _close_issues_enabled():
            try:
                await jelly_close(issue_id, silent=True)
            except Exception as e:
                log.info("Close attempt failed but continuing (issue %s): %s", issue_id, e)
        return {"series": True, "queued": True, "deleted": deleted, "closed": True}

    return {"series": True, "queued": True, "deleted": deleted, "closed": False}
