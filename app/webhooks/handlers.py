from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Sequence, Tuple

from app.config import cfg, BOT_PREFIX
from app.logging import log
from app.services.jellyseerr import jelly_comment, jelly_close, jelly_fetch_issue, is_our_comment
from app.services import radarr as R
from app.services import sonarr as S


# -------------------------------------------------------------------
# Helpers
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
    return bool(getattr(cfg, "JELLYSEERR_CLOSE_ISSUES", getattr(cfg, "CLOSE_JELLYSEERR_ISSUES", True)))


def _ensure_prefixed(msg: str) -> str:
    return msg if msg.strip().startswith(BOT_PREFIX) else f"{BOT_PREFIX} {msg}"


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
# Keyword + payload parsing
# -------------------------------------------------------------------

def _classify_bucket(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip().lower()
    def any_in(needles: Sequence[str]) -> bool:
        return any(n for n in needles if n and n.strip().lower() in t)

    # video first to catch "no video"/"black screen"
    if any_in((cfg.TV_VIDEO_KEYWORDS or "").split(",")) or any_in((cfg.MOVIE_VIDEO_KEYWORDS or "").split(",")):
        return "video"
    if any_in((cfg.TV_AUDIO_KEYWORDS or "").split(",")) or any_in((cfg.MOVIE_AUDIO_KEYWORDS or "").split(",")):
        return "audio"
    if any_in((cfg.TV_SUBTITLE_KEYWORDS or "").split(",")) or any_in((cfg.MOVIE_SUBTITLE_KEYWORDS or "").split(",")):
        return "subtitle"
    if any_in((cfg.MOVIE_WRONG_KEYWORDS or "").split(",")):
        return "wrong"
    if any_in((cfg.TV_OTHER_KEYWORDS or "").split(",")) or any_in((cfg.MOVIE_OTHER_KEYWORDS or "").split(",")):
        return "other"
    return None


def _extract_season_episode(payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """
    Jellyseerr puts these in different places depending on event.
    Try a few common paths safely.
    """
    for obj in (payload.get("issue") or {}, payload.get("data") or {}, payload):
        for skey in ("affectedSeason", "season", "seasonNumber"):
            for ekey in ("affectedEpisode", "episode", "episodeNumber"):
                s = obj.get(skey)
                e = obj.get(ekey)
                if isinstance(s, int) and isinstance(e, int):
                    return s, e
                # strings -> ints
                try:
                    ss = int(s) if s is not None else None
                    ee = int(e) if e is not None else None
                    if ss is not None and ee is not None:
                        return ss, ee
                except Exception:
                    pass
    return None, None


def _last_human_comment_text(issue_json: Optional[Dict[str, Any]]) -> Optional[str]:
    if not issue_json:
        return None
    comments = issue_json.get("comments") or []
    if not isinstance(comments, list) or not comments:
        return None
    # newest last
    for c in reversed(comments):
        msg = c.get("message") or ""
        if not is_our_comment(msg):
            return msg
    return None


# -------------------------------------------------------------------
# Main handler
# -------------------------------------------------------------------

async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single public entrypoint used by router.
    We post only ONE final success comment if verification passes.
    Otherwise we stay quiet (issue remains open) except for missing-keyword coaching.
    """
    # Common fields
    event = (payload.get("event") or payload.get("type") or "").lower()
    issue = payload.get("issue") or {}
    media = payload.get("media") or {}
    media_type = (media.get("mediaType") or payload.get("mediaType") or issue.get("mediaType") or "").lower()
    issue_id = issue.get("id") or payload.get("issueId") or payload.get("id")
    tmdb = media.get("tmdbId") or payload.get("tmdbId") or issue.get("tmdbId")
    tvdb = media.get("tvdbId") or payload.get("tvdbId") or issue.get("tvdbId")
    imdb = media.get("imdbId") or payload.get("imdbId") or issue.get("imdbId")

    # Fetch issue for: comments (loop guard + classification) + season/episode fallback
    issue_json = await jelly_fetch_issue(issue_id)
    last_comment = _last_human_comment_text(issue_json)
    if last_comment:
        log.info("Jellyseerr: last comment on issue %s: %r", issue_id, last_comment)

    # Season/Episode
    season, episode = _extract_season_episode(payload)
    if season is None or episode is None:
        # try to read from issue object if API returned them
        s = (issue_json or {}).get("season")
        e = (issue_json or {}).get("episode")
        try:
            season = int(s) if season is None and s is not None else season
            episode = int(e) if episode is None and e is not None else episode
        except Exception:
            pass

    # Bucket by current comment (if present), else last human comment
    comment_text = (payload.get("comment") or {}).get("message") or (payload.get("comment") or {}).get("text")
    bucket = _classify_bucket(comment_text) or _classify_bucket(last_comment)

    log.info(
        "Webhook event=%s issue_id=%s media_type=%s tmdb=%s tvdb=%s imdb=%s season=%s episode=%s bucket=%s",
        event or "?", issue_id, media_type or "?", tmdb, tvdb, imdb, season, episode, bucket
    )

    # Loop prevention: ignore if our own last comment just fired the webhook
    cd, remain = _under_cooldown(issue_id)
    if cd:
        log.info("Issue %s under cooldown (%ss remaining) — skipping.", issue_id, remain)
        return {"cooldown": True}

    # Missing keyword coaching (only if no bucket)
    if not bucket:
        msg = _ensure_prefixed(
            "Tip: include one of these keywords next time so I can repair this automatically: "
            "audio: no audio, no sound, audio issue, wrong language, not in english | "
            "video: no video, video missing, bad video, broken video, black screen | "
            "subtitle: missing subs, no subtitles, bad subtitles, wrong subs, subs out of sync"
        )
        if comment_text and not is_our_comment(comment_text) and issue_id:
            await jelly_comment(issue_id, msg, force_prefix=False)
            _arm_cooldown(issue_id)
        return {"coached": True}

    # Route by media type
    if media_type == "movie":
        detail = await _handle_movie(issue_id, tmdb, imdb, bucket)
        _arm_cooldown(issue_id)
        return detail

    # For TV, we only act if we have an exact episode to target.
    if media_type in ("tv", "show", "series"):
        if season is None or episode is None:
            log.info("Series missing season/episode. Not acting to avoid season-wide search.")
            return {"series": True, "skipped": "no-season-episode"}
        detail = await _handle_series(issue_id, tvdb, season, episode, bucket)
        _arm_cooldown(issue_id)
        return detail

    log.info("Unknown media_type=%r; ignoring.", media_type)
    return {"ignored": True}


# -------------------------------------------------------------------
# Movie flow
# -------------------------------------------------------------------

async def _handle_movie(issue_id: Optional[int], tmdb: Optional[int], imdb: Optional[str], bucket: Optional[str]) -> Dict[str, Any]:
    if not tmdb and not imdb:
        return {"movie": True, "skipped": "no-ids"}

    movie = await R.get_movie_by_tmdb(tmdb) if tmdb else await R.get_movie_by_imdb(imdb)
    if not movie:
        return {"movie": True, "skipped": "not-in-radarr"}
    movie_id = int(movie["id"])
    title = movie.get("title") or "This title"

    # Always delete immediately (best effort)
    deleted = await R.delete_moviefiles(movie_id)

    # Trigger search
    await R.search_movie(movie_id)

    # Verify via queue or recent 'grabbed'
    ok = await _poll_until(lambda: R.queue_has_movie(movie_id), cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC) \
         or await _poll_until(lambda: R.history_has_recent_grab(movie_id, cfg.RADARR_VERIFY_GRAB_SEC), cfg.RADARR_VERIFY_GRAB_SEC, cfg.RADARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        # Post single final message and close
        tmpl = (cfg.MSG_MOVIE_REPLACED_AND_GRABBED if deleted else cfg.MSG_MOVIE_SEARCH_ONLY_GRABBED) or "{title}: new download grabbed. Closing this issue."
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
# Series flow (episode only)
# -------------------------------------------------------------------

async def _handle_series(issue_id: Optional[int], tvdb: Optional[int], season: int, episode: int, bucket: Optional[str]) -> Dict[str, Any]:
    if not tvdb:
        return {"series": True, "skipped": "no-tvdb"}

    series = await S.get_series_by_tvdb(tvdb)
    if not series:
        return {"series": True, "skipped": "not-in-sonarr"}
    series_id = int(series["id"])
    title = series.get("title") or "This show"

    # Identify the exact episode ids
    ep_ids = await S.find_episode_ids(series_id, season, episode)
    if not ep_ids:
        log.info("Sonarr: could not map S%02dE%02d to episode ids; not acting.", season, episode)
        return {"series": True, "skipped": "no-episode-ids"}

    # Delete only those episode files
    deleted = await S.delete_episodefiles_by_episode_ids(series_id, ep_ids)

    # Search only those episode ids
    await S.search_episode_ids(ep_ids)

    # Verify (queue or recent grabbed) for those episode ids
    ok = await _poll_until(lambda: S.queue_has_any_of_episode_ids(ep_ids), cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC) \
         or await _poll_until(lambda: S.history_has_recent_grab_for_episode_ids(series_id, ep_ids, cfg.SONARR_VERIFY_GRAB_SEC), cfg.SONARR_VERIFY_GRAB_SEC, cfg.SONARR_VERIFY_POLL_SEC)

    if ok and issue_id:
        # One final message + close
        base_tmpl = (cfg.MSG_TV_REPLACED_AND_GRABBED if deleted else cfg.MSG_TV_SEARCH_ONLY_GRABBED) or "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue."
        msg = _ensure_prefixed(base_tmpl.format(title=title, season=season, episode=episode, deleted=deleted))
        await jelly_comment(issue_id, msg, force_prefix=False)
        if _close_issues_enabled():
            try:
                await jelly_close(issue_id, silent=True)
            except Exception as e:
                log.info("Close attempt failed but continuing (issue %s): %s", issue_id, e)
        return {"series": True, "queued": True, "deleted": deleted, "closed": True}

    return {"series": True, "queued": True, "deleted": deleted, "closed": False}
