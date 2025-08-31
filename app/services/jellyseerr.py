# app/services/jellyseerr.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import cfg
from app.logging import log

def _headers() -> Dict[str, str]:
    return {
        "X-Api-Key": cfg.JELLYSEERR_API_KEY,
        "Authorization": f"Bearer {cfg.JELLYSEERR_API_KEY}",
        "Content-Type": "application/json",
    }

def _base() -> str:
    return cfg.JELLYSEERR_URL.rstrip("/")

async def jelly_comment(issue_id: Any, message: str) -> bool:
    """
    Post a comment to an issue. Tries both known paths:
      - /api/v1/issue/{id}/comment
      - /api/v1/issues/{id}/comments   (plural)
    Returns True on 200/201/204, else False.
    """
    if not (_base() and cfg.JELLYSEERR_API_KEY and issue_id):
        return False

    paths = [
        f"/api/v1/issue/{issue_id}/comment",
        f"/api/v1/issues/{issue_id}/comments",
    ]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            try:
                r = await c.post(f"{_base()}{p}", headers=_headers(), json={"message": message})
                if r.status_code in (200, 201, 204):
                    return True
                log.info("jelly_comment %s -> %s %s", p, r.status_code, r.text[:180])
            except Exception as e:
                log.info("jelly_comment error %s: %s", p, e)
    return False

async def jelly_close(issue_id: Any) -> bool:
    """
    Best-effort close. Many servers expose POST /api/v1/issue/{id}/resolved (200 OK).
    Tries a few variants and returns True on success.
    """
    if not (_base() and cfg.JELLYSEERR_API_KEY and issue_id):
        return False

    attempts: Tuple[Tuple[str, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]], ...] = (
        ("POST", f"/api/v1/issue/{issue_id}/resolved", None, None),
        # legacy/variants sometimes seen
        ("POST", f"/api/v1/issue/{issue_id}/resolve", None, {"status": "resolved"}),
        ("POST", f"/api/v1/issue/{issue_id}/status", {"status": "resolved"}, None),
    )

    async with httpx.AsyncClient(timeout=20) as c:
        for method, path, json_body, query in attempts:
            try:
                r = await c.request(method, f"{_base()}{path}",
                                    headers=_headers(), json=json_body, params=query)
                if r.status_code in (200, 201, 204):
                    return True
                log.info("jelly_close %s %s -> %s %s", method, path, r.status_code, r.text[:180])
            except Exception as e:
                log.info("jelly_close error %s %s", path, e)
    return False

async def jelly_fetch_issue(issue_id: Any) -> Optional[Dict[str, Any]]:
    """
    Fetch full issue JSON (first path that returns !404). Returns dict or None.
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
