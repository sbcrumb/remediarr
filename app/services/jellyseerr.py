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

async def jelly_close(issue_id: int) -> None:
    """Resolve/close issue using multiple fallbacks (different Jellyseerr builds vary)."""
    client = _client_lazy()
    tried = []

    # 1) Likely UI endpoint
    url1 = f"{BASE}/api/v1/issue/{issue_id}/resolve"
    tried.append(url1)
    r = await client.post(url1, headers=_headers)
    if r.status_code in (200, 204):
        return
    # 2) PATCH status-style
    url2 = f"{BASE}/api/v1/issue/{issue_id}"
    tried.append(url2)
    r2 = await client.patch(url2, headers=_headers, json={"status": "resolved"})
    if r2.status_code in (200, 204):
        return
    # 3) numeric status fallback
    r3 = await client.patch(url2, headers=_headers, json={"status": 2})
    if r3.status_code in (200, 204):
        return

    log.warning("Jellyseerr: could not resolve issue %s (tried: %s). Last status=%s/%s/%s",
                issue_id, ", ".join(tried), r.status_code, r2.status_code, r3.status_code)
