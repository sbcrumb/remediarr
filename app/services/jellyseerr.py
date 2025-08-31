from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import cfg, BOT_PREFIX
from app.logging import log


def _headers() -> Dict[str, str]:
    return {
        "X-Api-Key": cfg.JELLYSEERR_API_KEY,
        "Authorization": f"Bearer {cfg.JELLYSEERR_API_KEY}",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return cfg.JELLYSEERR_URL.rstrip("/")


def _with_prefix(msg: str) -> str:
    m = (msg or "").lstrip()
    if not m:
        return m
    if not m.startswith(BOT_PREFIX):
        return f"{BOT_PREFIX} {msg}".strip()
    return msg


def is_our_comment(text: Optional[str]) -> bool:
    if not text:
        return False
    return text.lstrip().startswith(BOT_PREFIX)


async def jelly_comment(issue_id: Any, message: str, *, force_prefix: bool = True) -> bool:
    """
    Post a comment to a Jellyseerr issue. We try both v1 paths for compatibility.
    """
    if not (_base() and cfg.JELLYSEERR_API_KEY and issue_id):
        return False

    payload_msg = _with_prefix(message) if force_prefix else (message or "")
    paths = [
        f"/api/v1/issue/{issue_id}/comment",
        f"/api/v1/issues/{issue_id}/comments",
    ]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            try:
                r = await c.post(f"{_base()}{p}", headers=_headers(), json={"message": payload_msg})
                if r.status_code in (200, 201, 204):
                    return True
                log.info("jelly_comment %s -> %s %s", p, r.status_code, r.text[:180])
            except Exception as e:
                log.info("jelly_comment error %s: %s", p, e)
    return False


async def jelly_close(issue_id: Any) -> bool:
    """
    Resolve/close an issue. If close message is set, post it after a successful close.
    """
    if not (_base() and cfg.JELLYSEERR_API_KEY and issue_id):
        return False

    attempts: Tuple[Tuple[str, str, Optional[Dict[str, Any]]], ...] = (
        ("POST", f"/api/v1/issue/{issue_id}/resolved", None),
        ("POST", f"/api/v1/issue/{issue_id}/resolve", None),
        ("POST", f"/api/v1/issue/{issue_id}/status", {"status": "resolved"}),
    )

    async with httpx.AsyncClient(timeout=20) as c:
        for _, path, body in attempts:
            try:
                r = await c.post(f"{_base()}{path}", headers=_headers(), json=body)
                if r.status_code in (200, 204):
                    if cfg.JELLYSEERR_CLOSE_MESSAGE:
                        await jelly_comment(issue_id, cfg.JELLYSEERR_CLOSE_MESSAGE, force_prefix=True)
                    return True
                log.info("jelly_close %s -> %s %s", path, r.status_code, r.text[:180])
            except Exception as e:
                log.info("jelly_close error %s: %s", path, e)
    return False


async def jelly_fetch_issue(issue_id: Any) -> Optional[Dict[str, Any]]:
    """
    Fetch an issue (including recent comments) for de-dupe/guard logic.
    Returns raw JSON dict or None.
    """
    if not (_base() and cfg.JELLYSEERR_API_KEY and issue_id):
        return None

    paths = [
        f"/api/v1/issue/{issue_id}",
        f"/api/v1/issues/{issue_id}",
    ]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            try:
                r = await c.get(f"{_base()}{p}", headers=_headers())
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                log.info("jelly_fetch_issue HTTP error %s: %s", p, e)
            except Exception as e:
                log.info("jelly_fetch_issue error %s: %s", p, e)
    return None
