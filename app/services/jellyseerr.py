import os
import logging
from typing import Any, Dict, Optional, Tuple, List

import httpx

log = logging.getLogger("remediarr")

JELLYSEERR_URL = os.getenv("JELLYSEERR_URL", "").rstrip("/")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "")
JELLYSEERR_BOT_COMMENT_PREFIX = os.getenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]").strip()

def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if JELLYSEERR_API_KEY:
        h["X-Api-Key"] = JELLYSEERR_API_KEY
    return h

def _ensure_prefix(text: str) -> str:
    if not JELLYSEERR_BOT_COMMENT_PREFIX:
        return text
    if text.strip().startswith(JELLYSEERR_BOT_COMMENT_PREFIX):
        return text
    return f"{JELLYSEERR_BOT_COMMENT_PREFIX} {text}"

def is_our_comment(text: Optional[str]) -> bool:
    return bool(text and text.strip().startswith(JELLYSEERR_BOT_COMMENT_PREFIX))

async def jelly_post_comment(issue_id: int, text: str) -> None:
    msg = _ensure_prefix(text)
    log.info("Jellyseerr: posting comment to issue %s: %r", issue_id, msg)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{JELLYSEERR_URL}/api/v1/issue/{issue_id}/comment",
                         json={"message": msg}, headers=_headers())
        r.raise_for_status()

async def jelly_close(issue_id: int, silent: bool = True) -> bool:
    """Resolve the issue. Uses /resolved; falls back to PUT if needed."""
    params = {"noComment": "true"} if silent else None
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{JELLYSEERR_URL}/api/v1/issue/{issue_id}/resolved",
                         params=params, headers=_headers())
        if r.status_code in (404, 405):
            # some builds
            payload = {"status": 2}  # RESOLVED
            r = await c.put(f"{JELLYSEERR_URL}/api/v1/issue/{issue_id}",
                            json=payload, headers=_headers())
        try:
            r.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            log.info("Jellyseerr close failed for issue %s: %s", issue_id, e)
            return False

async def jelly_fetch_issue(issue_id: int) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{JELLYSEERR_URL}/api/v1/issue/{issue_id}",
                        headers=_headers())
        r.raise_for_status()
        return r.json() or {}

def _key_looks_like(key: str, needles: List[str]) -> bool:
    s = key.lower()
    for n in needles:
        if n in s:
            return True
    return False

def _maybe_int_from_obj(obj: Any, keys: List[str]) -> Optional[int]:
    if isinstance(obj, int):
        return obj
    if isinstance(obj, str):
        try:
            return int(obj)
        except Exception:
            return None
    if isinstance(obj, dict):
        for k in obj.keys():
            if _key_looks_like(k, keys):
                try:
                    return int(obj[k])
                except Exception:
                    continue
    return None

def _walk_for_season_episode(data: Any) -> Tuple[Optional[int], Optional[int]]:
    if data is None:
        return None, None
    if isinstance(data, dict):
        s = _maybe_int_from_obj(data.get("affected_season"), ["season"])
        e = _maybe_int_from_obj(data.get("affected_episode"), ["episode"])
        if s is not None or e is not None:
            return s, e
        for v in data.values():
            s2, e2 = _walk_for_season_episode(v)
            if s2 is not None or e2 is not None:
                return s2, e2
        return None, None
    if isinstance(data, list):
        for v in data:
            s2, e2 = _walk_for_season_episode(v)
            if s2 is not None or e2 is not None:
                return s2, e2
    return None, None

async def jelly_get_season_episode(issue_id: int) -> Tuple[Optional[int], Optional[int]]:
    data = await jelly_fetch_issue(issue_id)
    return _walk_for_season_episode(data)

async def jelly_last_comment(issue_id: int) -> Optional[str]:
    data = await jelly_fetch_issue(issue_id)
    comments = (data or {}).get("comments") or []
    if comments:
        last = comments[-1]
        text = last.get("message") or last.get("text") or ""
        log.info("Jellyseerr: last comment on issue %s: %r", issue_id, text)
        return text
    return None
