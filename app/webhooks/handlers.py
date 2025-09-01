import os
import re
import time
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.services.jellyseerr import jelly_comment, jelly_close, is_our_comment, jelly_fetch_issue
import app.services.radarr as R
import app.services.sonarr as S

log = logging.getLogger("remediarr")

# Cooldown (avoid loop on our own comment webhook)
ISSUE_COOLDOWN_SEC = int(os.getenv("REMEDIARR_ISSUE_COOLDOWN_SEC", "60"))

# Verify windows
RADARR_VERIFY_GRAB_SEC = int(os.getenv("RADARR_VERIFY_GRAB_SEC", "60"))
RADARR_VERIFY_POLL_SEC = int(os.getenv("RADARR_VERIFY_POLL_SEC", "5"))
SONARR_VERIFY_GRAB_SEC = int(os.getenv("SONARR_VERIFY_GRAB_SEC", "60"))
SONARR_VERIFY_POLL_SEC = int(os.getenv("SONARR_VERIFY_POLL_SEC", "5"))

# Messages (without prefix; services will prefix)
MSG_TV_REPLACED_AND_GRABBED = os.getenv(
    "MSG_TV_REPLACED_AND_GRABBED",
    "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_TV_SEARCH_ONLY_GRABBED = os.getenv(
    "MSG_TV_SEARCH_ONLY_GRABBED",
    "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_MOVIE_REPLACED_AND_GRABBED = os.getenv(
    "MSG_MOVIE_REPLACED_AND_GRABBED",
    "{title}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_MOVIE_SEARCH_ONLY_GRABBED = os.getenv(
    "MSG_MOVIE_SEARCH_ONLY_GRABBED",
    "{title}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_AUTOCLOSE_FAIL = os.getenv(
    "MSG_AUTOCLOSE_FAIL",
    "Action completed but I couldn’t auto-close this issue. Please close it once you verify it’s fixed.",
)

# Keywords
def _csv(name: str, default: str) -> list[str]:
    return [x.strip().lower() for x in os.getenv(name, default).split(",") if x.strip()]

TV_AUDIO = lambda: _csv("TV_AUDIO_KEYWORDS", "no audio,no sound,missing audio,audio issue,wrong language,not in english")
TV_VIDEO = lambda: _csv("TV_VIDEO_KEYWORDS", "no video,video glitch,black screen,stutter,pixelation")
TV_SUB   = lambda: _csv("TV_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
TV_OTHER = lambda: _csv("TV_OTHER_KEYWORDS", "buffering,playback error,corrupt file")

MOV_AUDIO = lambda: _csv("MOVIE_AUDIO_KEYWORDS", "no audio,no sound,audio issue,wrong language,not in english")
MOV_VIDEO = lambda: _csv("MOVIE_VIDEO_KEYWORDS", "no video,video missing,bad video,broken video,black screen")
MOV_SUB   = lambda: _csv("MOVIE_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
MOV_OTHER = lambda: _csv("MOVIE_OTHER_KEYWORDS", "buffering,playback error,corrupt file")
MOV_WRONG = lambda: _csv("MOVIE_WRONG_KEYWORDS", "not the right movie,wrong movie,incorrect movie")

_last_action: dict[int, float] = {}  # issue_id -> unix time


def _cooldown_active(issue_id: int) -> Optional[int]:
    now = time.time()
    last = _last_action.get(issue_id, 0)
    remaining = int(ISSUE_COOLDOWN_SEC - (now - last))
    return remaining if remaining > 0 else None


def _start_cooldown(issue_id: int) -> None:
    _last_action[issue_id] = time.time()


def _match_bucket(text: str, media_type: str) -> Optional[str]:
    s = (text or "").lower()
    if media_type == "movie":
        if any(k in s for k in MOV_WRONG()):
            return "wrong"
        if any(k in s for k in MOV_AUDIO()):
            return "audio"
        if any(k in s for k in MOV_VIDEO()):
            return "video"
        if any(k in s for k in MOV_SUB()):
            return "subtitle"
        if any(k in s for k in MOV_OTHER()):
            return "other"
    else:
        if any(k in s for k in TV_AUDIO()):
            return "audio"
        if any(k in s for k in TV_VIDEO()):
            return "video"
        if any(k in s for k in TV_SUB()):
            return "subtitle"
        if any(k in s for k in TV_OTHER()):
            return "other"
    return None


def _parse_payload(p: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts your nested payload shape and normal variations.
    Normalizes keys we use downstream.
    """
    media = p.get("media") or {}
    issue = p.get("issue") or {}
    comment = p.get("comment") or {}

    media_type = (media.get("media_type") or media.get("mediaType") or "").lower() or None
    tmdb = media.get("tmdbId") or media.get("tmdbid")
    tvdb = media.get("tvdbId") or media.get("tvdbid")
    imdb = media.get("imdbId") or media.get("imdbid")

    issue_id = issue.get("issue_id") or issue.get("id")
    if isinstance(issue_id, str) and issue_id.isdigit():
        issue_id = int(issue_id)

    season = issue.get("affected_season") or issue.get("affectedSeason")
    episode = issue.get("affected_episode") or issue.get("affectedEpisode")

    event = (p.get("event") or "").lower()
    subject = p.get("subject") or ""
    message = p.get("message") or ""
    comment_text = comment.get("comment_message") or comment.get("message") or ""

    return {
        "event": event,
        "subject": subject,
        "message": message,
        "media_type": media_type,
        "tmdb": tmdb,
        "tvdb": tvdb,
        "imdb": imdb,
        "issue_id": issue_id,
        "season": season,
        "episode": episode,
        "comment_text": comment_text,
    }


async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    info = _parse_payload(payload)

    # Fill missing fields from Jellyseerr issue if we have an issue_id
    issue_id = info.get("issue_id")
    event = info["event"]
    media_type = info["media_type"] or "?"

    # If we can fetch more metadata, do it
    if issue_id:
        fetched = await jelly_fetch_issue(issue_id)
        last_comment = fetched.get("last_comment", "")
        if last_comment:
            log.info("Keyword scan (fallback last human comment): %r", last_comment)
        # prefer webhook values, but backfill season/episode/media ids if missing
        if not info.get("season"):
            info["season"] = fetched.get("affected_season")
        if not info.get("episode"):
            info["episode"] = fetched.get("affected_episode")
        if not info.get("media_type"):
            info["media_type"] = fetched.get("media_type") or info["media_type"]
        if not info.get("tmdb"):
            info["tmdb"] = fetched.get("tmdbId")
        if not info.get("tvdb"):
            info["tvdb"] = fetched.get("tvdbId")
        info["last_comment"] = last_comment
        info["title"] = fetched.get("title")

    media_type = info.get("media_type") or "?"
    tmdb = info.get("tmdb")
    tvdb = info.get("tvdb")
    imdb = info.get("imdb")
    season = info.get("season")
    episode = info.get("episode")
    last_comment = info.get("last_comment") or info.get("comment_text") or info.get("message") or ""
    bucket = _match_bucket(last_comment, media_type) if last_comment else None

    log.info(
        "Webhook event=%s issue_id=%s media_type=%s tmdb=%s tvdb=%s imdb=%s season=%s episode=%s bucket=%s",
        event or "?", issue_id, media_type, tmdb, tvdb, imdb, season, episode, bucket
    )

    # If it's our own comment (prefix check) and the cooldown hasn't elapsed, skip
    if issue_id:
        cd = _cooldown_active(issue_id)
        if cd:
            log.info("Issue %s under cooldown (%ss remaining) — skipping repeat action.", issue_id, cd)
            return {"status": "cooldown"}

    # Only act on comments/issues with recognizable keywords
    if bucket is None:
        return {"status": "no-op", "reason": "no keywords"}

    if media_type == "movie":
        if not tmdb and not imdb:
            return {"status": "no-op", "reason": "no tmdb/imdb for movie"}

        # ---- MOVIE FLOW ----
        # Find movie
        movie = await (R.get_movie_by_tmdb(tmdb) if tmdb else R.get_movie_by_imdb(imdb))
        if not movie:
            return {"status": "no-op", "reason": "movie not found in radarr"}

        movie_id = movie.get("id")
        title = movie.get("title") or info.get("title") or "This title"

        # Baseline history before we act
        baseline = await R.latest_grab_timestamp(movie_id)

        # Replace when appropriate (audio/video/sub/sub/other); for "wrong" simply search
        removed = 0
        if bucket in ("audio", "video", "subtitle", "other"):
            removed = await R.delete_movie_files(movie_id)

        # Search (always)
        try:
            await R.trigger_movie_search(movie_id)
        except httpx.HTTPStatusError as e:
            log.info("Radarr POST /api/v3/command failed: %s", e)
            # still try to verify in case queue already has it
        ok = await R.wait_for_new_grab_or_queue(
            movie_id, baseline, total_sec=RADARR_VERIFY_GRAB_SEC, poll_sec=RADARR_VERIFY_POLL_SEC
        )
        if ok:
            # Post single final comment, then close
            msg = (
                MSG_MOVIE_REPLACED_AND_GRABBED.format(title=title, deleted=removed)
                if removed
                else MSG_MOVIE_SEARCH_ONLY_GRABBED.format(title=title)
            )
            await jelly_comment(issue_id, msg)
            _start_cooldown(issue_id)
            closed = await jelly_close(issue_id)
            if not closed:
                await jelly_comment(issue_id, MSG_AUTOCLOSE_FAIL)
            return {"status": "movie-ok", "removed": removed, "closed": closed}

        # No grab detected — do not comment/close
        return {"status": "movie-search-no-grab", "removed": removed}

    elif media_type in ("tv", "series", "show"):
        if not tvdb:
            return {"status": "no-op", "reason": "no tvdb for series"}
        if not (isinstance(season, int) and isinstance(episode, int)):
            log.info("Series missing season/episode. Not acting to avoid season-wide search.")
            return {"status": "no-op", "reason": "no season/episode"}

        # ---- TV FLOW (single episode only) ----
        series = await S.get_series_by_tvdb(tvdb)
        if not series:
            return {"status": "no-op", "reason": "series not in sonarr"}
        series_id = series.get("id")
        title = series.get("title") or info.get("title") or "This series"

        ep = await S.find_episode(series_id, season, episode)
        if not ep:
            return {"status": "no-op", "reason": "episode not found"}

        episode_id = ep.get("id")

        baseline = await S.latest_episode_grab(series_id, episode_id)

        # Replace for most buckets; for unknown we can just search
        removed = 0
        if bucket in ("audio", "video", "subtitle", "other"):
            removed = await S.delete_episode_file_for_episode(episode_id)

        try:
            await S.trigger_episode_search(episode_id)
        except Exception as e:
            log.info("Sonarr EpisodeSearch failed: %s", e)

        ok = await S.wait_for_episode_grab(
            series_id, episode_id, baseline, total_sec=SONARR_VERIFY_GRAB_SEC, poll_sec=SONARR_VERIFY_POLL_SEC
        )
        if ok:
            msg = (
                MSG_TV_REPLACED_AND_GRABBED.format(title=title, season=season, episode=episode)
                if removed
                else MSG_TV_SEARCH_ONLY_GRABBED.format(title=title, season=season, episode=episode)
            )
            await jelly_comment(issue_id, msg)
            _start_cooldown(issue_id)
            closed = await jelly_close(issue_id)
            if not closed:
                await jelly_comment(issue_id, MSG_AUTOCLOSE_FAIL)
            return {"status": "tv-ok", "removed": removed, "closed": closed}

        return {"status": "tv-search-no-grab", "removed": removed}

    else:
        return {"status": "no-op", "reason": f"unsupported media_type {media_type}"}
