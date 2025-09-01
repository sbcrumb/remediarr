import os
import logging
from typing import Optional, Tuple, Dict, Any

import httpx

log = logging.getLogger("remediarr")

JELLYSEERR_URL = os.getenv("JELLYSEERR_URL", "").rstrip("/")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "")
COMMENT_PREFIX = "[Remediarr]"  # hardcoded to prevent loops

HEADERS = {"X-Api-Key": JELLYSEERR_API_KEY}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=JELLYSEERR_URL, headers=HEADERS, timeout=30.0)


def _prefixed(msg: str) -> str:
    m = (msg or "").strip()
    if not m.startswith(COMMENT_PREFIX):
        return f"{COMMENT_PREFIX} {m}"
    return m


async def jelly_comment(issue_id: int, message: str) -> None:
    msg = _prefixed(message)
    log.info("Jellyseerr: posting comment to issue %s: %r", issue_id, msg)
    async with _client() as c:
        r = await c.post(f"/api/v1/issue/{issue_id}/comment", json={"message": msg})
        r.raise_for_status()


async def jelly_close(issue_id: int) -> bool:
    """
    Close an issue using the most compatible endpoint:
    1) POST /api/v1/issue/{id}/resolve
    2) PATCH /api/v1/issue/{id}  body: {"status": 2}
    Returns True on success, False on failure.
    """
    async with _client() as c:
        # 1) resolve endpoint (preferred)
        try:
            r = await c.post(f"/api/v1/issue/{issue_id}/resolve")
            if r.status_code < 400:
                return True
        except httpx.HTTPError:
            pass

        # 2) PATCH fallback
        try:
            r = await c.patch(f"/api/v1/issue/{issue_id}", json={"status": 2})
            if r.status_code < 400:
                return True
        except httpx.HTTPError:
            pass

    return False


async def is_our_comment(text: str) -> bool:
    return (text or "").strip().startswith(COMMENT_PREFIX)


async def jelly_fetch_issue(issue_id: int) -> Dict[str, Any]:
    """
    Fetch issue details and return a compact dict:
      {
        "last_comment": "text or ''",
        "last_comment_is_ours": bool,
        "affected_season": int|None,
        "affected_episode": int|None,
        "media_type": "movie"|"tv"|None,
        "tmdbId": int|None,
        "tvdbId": int|None,
        "title": str|None
      }
    """
    async with _client() as c:
        r = await c.get(f"/api/v1/issue/{issue_id}")
        r.raise_for_status()
        data = r.json() or {}

    comments = data.get("comments") or []
    last_comment = comments[-1].get("message") if comments else ""
    last_comment_is_ours = await is_our_comment(last_comment or "")

    issue = data.get("issue") or {}
    media = data.get("media") or data.get("mediaInfo") or {}

    # try common fields
    affected_season = issue.get("affectedSeason") if isinstance(issue.get("affectedSeason"), int) else None
    affected_episode = issue.get("affectedEpisode") if isinstance(issue.get("affectedEpisode"), int) else None

    media_type = (media.get("mediaType") or issue.get("mediaType") or "").lower() or None
    tmdb_id = media.get("tmdbId") or issue.get("tmdbId")
    tvdb_id = media.get("tvdbId") or issue.get("tvdbId")
    title = media.get("title") or data.get("title")

    info = {
        "last_comment": last_comment or "",
        "last_comment_is_ours": bool(last_comment_is_ours),
        "affected_season": affected_season,
        "affected_episode": affected_episode,
        "media_type": media_type,
        "tmdbId": tmdb_id,
        "tvdbId": tvdb_id,
        "title": title,
    }
    if last_comment:
        log.info("Jellyseerr: last comment on issue %s: %r", issue_id, last_comment)
    return info
