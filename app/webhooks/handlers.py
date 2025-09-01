import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional, List

from fastapi import HTTPException

from app.services import jellyseerr as J
from app.services import radarr as R
from app.services import sonarr as S

log = logging.getLogger("remediarr")

def _split_keywords(envvar: str) -> List[str]:
    raw = (os.getenv(envvar) or "").strip()
    if not raw:
        return []
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

KW = {
    "tv": {
        "audio": _split_keywords("TV_AUDIO_KEYWORDS"),
        "video": _split_keywords("TV_VIDEO_KEYWORDS"),
        "subtitle": _split_keywords("TV_SUBTITLE_KEYWORDS"),
        "other": _split_keywords("TV_OTHER_KEYWORDS"),
    },
    "movie": {
        "audio": _split_keywords("MOVIE_AUDIO_KEYWORDS"),
        "video": _split_keywords("MOVIE_VIDEO_KEYWORDS"),
        "subtitle": _split_keywords("MOVIE_SUBTITLE_KEYWORDS"),
        "other": _split_keywords("MOVIE_OTHER_KEYWORDS"),
        "wrong": _split_keywords("MOVIE_WRONG_KEYWORDS"),
    },
}

JELLYSEERR_COACH_REPORTERS = (os.getenv("JELLYSEERR_COACH_REPORTERS","false").lower() == "true")
JELLYSEERR_CLOSE_ISSUES = (os.getenv("JELLYSEERR_CLOSE_ISSUES","true").lower() == "true")

RADARR_VERIFY_GRAB_SEC = int(os.getenv("RADARR_VERIFY_GRAB_SEC","60"))
RADARR_VERIFY_POLL_SEC = int(os.getenv("RADARR_VERIFY_POLL_SEC","5"))
SONARR_VERIFY_GRAB_SEC = int(os.getenv("SONARR_VERIFY_GRAB_SEC","60"))
SONARR_VERIFY_POLL_SEC = int(os.getenv("SONARR_VERIFY_POLL_SEC","5"))

MSG_MOVIE_REPLACED_AND_GRABBED = os.getenv("MSG_MOVIE_REPLACED_AND_GRABBED","{title}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.")
MSG_MOVIE_SEARCH_ONLY_GRABBED = os.getenv("MSG_MOVIE_SEARCH_ONLY_GRABBED","{title}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.")
MSG_TV_REPLACED_AND_GRABBED = os.getenv("MSG_TV_REPLACED_AND_GRABBED","{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.")
MSG_TV_SEARCH_ONLY_GRABBED = os.getenv("MSG_TV_SEARCH_ONLY_GRABBED","{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.")

def _bucket(media_type: str, text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    buckets = KW.get(media_type, {})
    for name, words in buckets.items():
        for w in words:
            if w and w in t:
                return name
    return None

def _first_human_comment_text(issue_json: Dict[str, Any]) -> Optional[str]:
    comments = issue_json.get("comments") or []
    for c in reversed(comments):
        txt = c.get("message") or c.get("text") or ""
        if not J.is_our_comment(txt):
            return txt
    return None

async def handle_jellyseerr(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Always fetch authoritative info from Jellyseerr
    issue = (payload.get("issue") or {})
    issue_id = issue.get("issue_id")
    if not issue_id:
        raise HTTPException(status_code=400, detail="Missing issue_id in webhook payload")

    full = await J.jelly_fetch_issue(int(issue_id))
    media = (full.get("media") or {})
    media_type = (media.get("media_type") or media.get("type") or "").lower()
    tmdb = (media.get("tmdbId") or media.get("tmdbid") or media.get("tmdb"))
    tvdb = (media.get("tvdbId") or media.get("tvdbid") or media.get("tvdb"))
    imdb = media.get("imdbId")

    # Try to resolve season/episode from issue body if missing in payload/template
    season = issue.get("affected_season")
    episode = issue.get("affected_episode")
    if season in (None, "{{affected_season}}") or episode in (None, "{{affected_episode}}"):
        s2, e2 = await J.jelly_get_season_episode(int(issue_id))
        season = season if isinstance(season, int) else s2
        episode = episode if isinstance(episode, int) else e2

    # Last human comment -> keywords
    last_human = _first_human_comment_text(full)
    log.info("Jellyseerr: last human comment on issue %s: %r", issue_id, last_human)
    bucket = _bucket("movie" if media_type == "movie" else "tv", last_human)
    log.info("Keyword scan: %r -> bucket=%s", last_human, bucket)

    if not bucket:
        if JELLYSEERR_COACH_REPORTERS and last_human:
            await J.jelly_post_comment(int(issue_id),
                "Tip: include one of the auto-fix keywords next time so I can repair this automatically.")
        return {"ok": True, "skipped": "no_keywords"}

    if media_type == "movie":
        return await _handle_movie(int(issue_id), tmdb=tmdb, imdb=imdb, bucket=bucket)
    elif media_type in ("tv", "series", "show"):
        return await _handle_tv(int(issue_id), tmdb=tmdb, tvdb=tvdb, season=season, episode=episode, bucket=bucket)
    return {"ok": True, "skipped": "unsupported_media"}

# -------- Movies --------

async def _handle_movie(issue_id: int, tmdb: Optional[int], imdb: Optional[str], bucket: str) -> Dict[str, Any]:
    movie = await (R.get_movie_by_tmdb(tmdb) if tmdb else R.get_movie_by_imdb(imdb))
    if not movie:
        raise HTTPException(status_code=404, detail=f"Movie not found in Radarr (tmdb={tmdb} imdb={imdb})")
    movie_id = movie["id"]
    title = movie.get("title") or "Unknown Title"

    baseline = await R.latest_grab_timestamp(movie_id)

    deleted = 0
    if bucket in ("video", "audio", "subtitle"):
        deleted = await R.delete_movie_files(movie_id)

    await R.trigger_search(movie_id)

    # verify
    deadline = time.time() + RADARR_VERIFY_GRAB_SEC
    grabbed = False
    while time.time() < deadline:
        latest = await R.latest_grab_timestamp(movie_id)
        if latest and (not baseline or latest > baseline):
            grabbed = True
            break
        await asyncio.sleep(RADARR_VERIFY_POLL_SEC)

    if grabbed:
        if JELLYSEERR_CLOSE_ISSUES:
            await J.jelly_close(issue_id, silent=True)
        msg = (MSG_MOVIE_REPLACED_AND_GRABBED if deleted else MSG_MOVIE_SEARCH_ONLY_GRABBED).format(title=title)
        await J.jelly_post_comment(issue_id, msg)
        return {"ok": True, "movie_id": movie_id, "deleted": deleted, "grabbed": True}

    return {"ok": True, "movie_id": movie_id, "deleted": deleted, "grabbed": False}

# -------- TV --------

async def _handle_tv(issue_id: int, tmdb: Optional[int], tvdb: Optional[int],
                     season: Optional[int], episode: Optional[int], bucket: str) -> Dict[str, Any]:
    if not (season and episode):
        log.info("Series missing season/episode. Not acting to avoid season-wide search.")
        return {"ok": True, "skipped": "no_episode"}

    series = await (S.get_series_by_tvdb(tvdb) if tvdb else S.get_series_by_tmdb(tmdb))
    if not series:
        raise HTTPException(status_code=404, detail=f"Series not found in Sonarr (tmdb={tmdb} tvdb={tvdb})")
    series_id = series["id"]
    title = series.get("title") or "Unknown Series"

    ep = await S.get_episode(series_id, season, episode)
    if not ep:
        raise HTTPException(status_code=404, detail=f"Episode not found: S{season:02d}E{episode:02d}")

    baseline = await S.latest_grab_timestamp(series_id)

    deleted = 0
    if bucket in ("video", "audio", "subtitle"):
        deleted = await S.delete_episode_files(series_id, [ep["id"]])

    await S.trigger_episode_search([ep["id"]])

    deadline = time.time() + SONARR_VERIFY_GRAB_SEC
    grabbed = False
    while time.time() < deadline:
        latest = await S.latest_grab_timestamp(series_id)
        if latest and (not baseline or latest > baseline):
            grabbed = True
            break
        await asyncio.sleep(SONARR_VERIFY_POLL_SEC)

    if grabbed:
        if JELLYSEERR_CLOSE_ISSUES:
            await J.jelly_close(issue_id, silent=True)
        msg = (MSG_TV_REPLACED_AND_GRABBED if deleted else MSG_TV_SEARCH_ONLY_GRABBED).format(
            title=title, season=season, episode=episode)
        await J.jelly_post_comment(issue_id, msg)
        return {"ok": True, "series_id": series_id, "episode_id": ep["id"], "deleted": deleted, "grabbed": True}

    return {"ok": True, "series_id": series_id, "episode_id": ep["id"], "deleted": deleted, "grabbed": False}
