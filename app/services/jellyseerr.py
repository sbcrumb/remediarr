import httpx
from typing import Any, Optional, Dict

from app.config import JELLYSEERR_URL, JELLYSEERR_API_KEY

def _headers():
    return {"X-Api-Key": JELLYSEERR_API_KEY, "Content-Type": "application/json"}

async def _simple_request(method: str, path: str, *, json: Optional[Dict] = None, params: Optional[Dict] = None) -> int:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method.upper(), f"{JELLYSEERR_URL}{path}", headers=_headers(), json=json, params=params)
        return r.status_code

async def comment_issue(issue_id: int, message: str) -> bool:
    code = await _simple_request("POST", f"/api/v1/issue/{issue_id}/comment", json={"message": message})
    return code in (200, 201, 204)
