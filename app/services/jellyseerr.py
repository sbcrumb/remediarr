from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from app.config import cfg, BOT_PREFIX
from app.logging import log


def _base() -> str:
    return (cfg.JELLYSEERR_URL or "").rstrip("/")


def _headers() -> Dict[str, str]:
    return {"X-Api-Key": cfg.JELLYSEERR_API_KEY or ""}


def _timeout() -> float:
    try:
        return float(getattr(cfg, "JELLYSEERR_HTTP_TIMEOUT", 30))
    except Exception:
        return 30.0


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base(), headers=_headers(), timeout=_timeout())


def is_our_comment(msg: Optional[str]) -> bool:
    if not msg:
        return False
    return msg.strip().startswith(BOT_PREFIX)


async def jelly_comment(issue_id: int, message: str, *, force_prefix: bool = False) -> None:
    """Post a comment. If force_prefix is False, assume the caller already prefixed."""
    msg = message
    if force_prefix and not is_our_comment(message):
        msg = f"{BOT_PREFIX} {message}"
    log.info("Jellyseerr: posting comment to issue %s: %r", issue_id, msg)
    async with _client() as c:
        r = await c.post(f"/api/v1/issue/{issue_id}/comment", json={"message": msg})
        r.raise_for_status()


async def jelly_fetch_issue(issue_id: int) -> Optional[Dict[str, Any]]:
    async with _client() as c:
        r = await c.get(f"/api/v1/issue/{issue_id}")
        r.raise_for_status()
        data = r.json()
        # Light log of last human comment
        comments = data.get("comments") or []
        last = None
        for cmt in reversed(comments):
            text = cmt.get("message")
            if not is_our_comment(text):
                last = text
                break
        if last is not None:
            log.info("Jellyseerr: last comment on issue %s: %r", issue_id, last)
        return data


async def jelly_list_issues(*, take: int = 50, skip: int = 0) -> List[Dict[str, Any]]:
    """List latest issues. We filter client-side for robustness."""
    async with _client() as c:
        r = await c.get("/api/v1/issue", params={"take": take, "skip": skip, "sort": "createdAt"})
        r.raise_for_status()
        data = r.json() or []
    return data if isinstance(data, list) else []


async def jelly_find_issue_by_media(
    *, tmdb: Optional[int] = None, tvdb: Optional[int] = None, imdb: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Some webhooks do not include issueId. In that case we grab recent issues and
    choose the newest matching media entry that is still open.
    """
    # Try a couple of pages just in case
    pages = [0, 50]
    for off in pages:
        items = await jelly_list_issues(take=50, skip=off)
        for it in items:
            media = it.get("media") or {}
            if (
                (tmdb and media.get("tmdbId") == tmdb)
                or (tvdb and media.get("tvdbId") == tvdb)
                or (imdb and media.get("imdbId") == imdb)
            ):
                # Prefer open issues
                status = (it.get("status") or "").lower()
                if status in ("open", 0, "0") or status == "" or status is None:
                    return it
                # If nothing open is found, still return the newest match
                return it
    return None


async def jelly_close(issue_id: int, *, silent: bool = True, close_message: Optional[str] = None) -> bool:
    """
    Close an issue. Jellyseerr variants differ; try the known patterns:
      1) POST /issue/{id}/resolve
      2) PATCH /issue/{id} {"status":2}
    """
    async with _client() as c:
        # Attempt #1: explicit resolve endpoint
        try:
            r = await c.post(f"/api/v1/issue/{issue_id}/resolve")
            if r.status_code // 100 == 2:
                if not silent and close_message:
                    await jelly_comment(issue_id, f"{BOT_PREFIX} {close_message}", force_prefix=False)
                return True
        except httpx.HTTPError as e:
            log.info("Jellyseerr resolve endpoint failed (will retry with PATCH): %s", e)

        # Attempt #2: PATCH set status=2 (resolved)
        try:
            r2 = await c.patch(f"/api/v1/issue/{issue_id}", json={"status": 2})
            if r2.status_code // 100 == 2:
                if not silent and close_message:
                    await jelly_comment(issue_id, f"{BOT_PREFIX} {close_message}", force_prefix=False)
                return True
            log.info("Jellyseerr PATCH close returned %s", r2.status_code)
        except httpx.HTTPError as e:
            log.info("Jellyseerr PATCH /issue/%s failed: %s", issue_id, e)

    return False
