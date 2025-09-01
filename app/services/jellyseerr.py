import os
import logging
from typing import Any, Dict, Optional, Tuple, List
import httpx

log = logging.getLogger("remediarr")

BASE = os.getenv("JELLYSEERR_URL", "").rstrip("/")
API_KEY = os.getenv("JELLYSEERR_API_KEY", "")
PREFIX = os.getenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]")

_headers = {"X-Api-Key": API_KEY} if API_KEY else {}

_client: Optional[httpx.AsyncClient] = None
def _client_lazy() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client

def is_our_comment(text: str) -> bool:
    return text.strip().startswith(PREFIX)

async def jelly_fetch_issue(issue_id: int) -> Dict[str, Any]:
    """GET /api/v1/issue/{id}"""
    url = f"{BASE}/api/v1/issue/{issue_id}"
    r = await _client_lazy().get(url, headers=_headers)
    r.raise_for_status()
    return r.json()

def _extract_issue_context(issue_json: Dict[str, Any]) -> Dict[str, Any]:
    # Media
    media = issue_json.get("media") or {}
    media_type = (media.get("mediaType") or media.get("type") or "").lower() or None
    tmdb = media.get("tmdbId") or None
    tvdb = media.get("tvdbId") or None

    # Affected S/E are top-level on Overseerr/Jellyseerr issue details
    affected_season = issue_json.get("affectedSeason")
    affected_episode = issue_json.get("affectedEpisode")
    try:
        season = int(affected_season) if affected_season not in (None, "", "null") else None
    except Exception:
        season = None
    try:
        episode = int(affected_episode) if affected_episode not in (None, "", "null") else None
    except Exception:
        episode = None

    # Last comment (prefer last human)
    comments: List[Dict[str, Any]] = issue_json.get("comments") or []
    last_text = ""
    for c in reversed(comments):
        body = (c.get("message") or c.get("text") or "").strip()
        if body and not is_our_comment(body):
            last_text = body
            break
    if not last_text and comments:
        last_text = (comments[-1].get("message") or comments[-1].get("text") or "").strip()

    return {
        "media_type": media_type, "tmdb": tmdb, "tvdb": tvdb,
        "season": season, "episode": episode,
        "last_human_comment": last_text
    }

async def jelly_last_human_comment(issue_id: int) -> str:
    ctx = _extract_issue_context(await jelly_fetch_issue(issue_id))
    return ctx["last_human_comment"]

async def jelly_comment(issue_id: int, text: str) -> None:
    body = {"message": text}
    url = f"{BASE}/api/v1/issue/{issue_id}/comment"
    log.info("Jellyseerr: posting comment to issue %s: %r", issue_id, text)
    r = await _client_lazy().post(url, headers=_headers, json=body)
    r.raise_for_status()

async def jelly_close(issue_id: int) -> bool:
    """Resolve/close issue using multiple fallbacks (different Jellyseerr builds vary)."""
    if not (BASE and API_KEY):
        log.warning("Cannot close issue %s: missing Jellyseerr config", issue_id)
        return False
        
    client = _client_lazy()
    
    # Try different endpoints that various Jellyseerr/Overseerr versions support
    endpoints = [
        # Most common working endpoint
        ("POST", f"/api/v1/issue/{issue_id}/resolve", {}),
        # Alternative endpoints to try
        ("PATCH", f"/api/v1/issue/{issue_id}", {"status": "resolved"}),
        ("PUT", f"/api/v1/issue/{issue_id}", {"status": "resolved"}),
        ("POST", f"/api/v1/issue/{issue_id}/resolved", {}),
        ("PATCH", f"/api/v1/issue/{issue_id}", {"status": 2}),  # numeric status
        ("PUT", f"/api/v1/issue/{issue_id}/status", {"status": "resolved"}),
    ]

    for method, path, json_data in endpoints:
        try:
            url = f"{BASE}{path}"
            log.info("Attempting to close issue %s: %s %s", issue_id, method, path)
            
            if json_data:
                r = await client.request(method, url, headers=_headers, json=json_data)
            else:
                r = await client.request(method, url, headers=_headers)
                
            log.info("Close attempt result: %s %s -> %s", method, path, r.status_code)
            
            if r.status_code in (200, 201, 204):
                log.info("Successfully closed issue %s using %s %s", issue_id, method, path)
                return True
                
        except Exception as e:
            log.info("Close attempt failed %s %s: %s", method, path, e)
            continue

    log.warning("All close attempts failed for issue %s", issue_id)
    return False