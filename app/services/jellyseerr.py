from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.config import cfg, BOT_PREFIX
from app.logging import log


def _base() -> str:
    return cfg.JELLYSEERR_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"X-Api-Key": cfg.JELLYSEERR_API_KEY, "Content-Type": "application/json"}


def _timeout() -> float:
    try:
        return float(getattr(cfg, "JELLYSEERR_HTTP_TIMEOUT", 30))
    except Exception:
        return 30.0


def is_our_comment(text: str | None) -> bool:
    return bool(text and text.strip().startswith(BOT_PREFIX))


async def jelly_fetch_issue(issue_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not issue_id:
        return None
    url = f"{_base()}/api/v1/issue/{int(issue_id)}"
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(url, headers=_headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def jelly_comment(issue_id: int, message: str, *, force_prefix: bool = False) -> bool:
    if not issue_id or not message:
        return False
    msg = message if (not force_prefix or message.strip().startswith(BOT_PREFIX)) else f"{BOT_PREFIX} {message}"
    url = f"{_base()}/api/v1/issue/{int(issue_id)}/comment"
    payload = {"message": msg}
    log.info("Jellyseerr: posting comment to issue %s: %r", issue_id, msg)
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.post(url, headers=_headers(), json=payload)
        r.raise_for_status()
        return True


async def jelly_close(issue_id: int, *, silent: bool = True, message: Optional[str] = None) -> bool:
    """
    Close the issue. If silent=True, do not post a separate close comment.
    If silent=False and a message is provided (or configured), post it first, then close.
    """
    if not issue_id:
        return False

    close_msg = (message or (cfg.JELLYSEERR_CLOSE_MESSAGE or "")).strip()
    if not silent and close_msg:
        await jelly_comment(issue_id, f"{BOT_PREFIX} {close_msg}", force_prefix=False)

    # Jellyseerr uses PATCH for updates (PUT returns 405).
    url = f"{_base()}/api/v1/issue/{int(issue_id)}"
    payload = {"status": 2}  # 2 = resolved
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.patch(url, headers=_headers(), json=payload)
        r.raise_for_status()
        log.info("Jellyseerr: issue %s closed (status=%s)", issue_id, r.status_code)
        return True