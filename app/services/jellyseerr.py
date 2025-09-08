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
    issue_data = r.json()
    
    # Log the raw issue data to debug season/episode extraction
    log.info("Raw Jellyseerr issue data for %s: affectedSeason=%s, affectedEpisode=%s", 
             issue_id, issue_data.get("affectedSeason"), issue_data.get("affectedEpisode"))
    
    return issue_data

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

async def jelly_get_last_comment_user(issue_id: int) -> Optional[int]:
    """Get the user ID of the person who made the last human comment."""
    try:
        issue_data = await jelly_fetch_issue(issue_id)
        comments: List[Dict[str, Any]] = issue_data.get("comments") or []
        
        # Find the last human comment (not from Remediarr)
        for c in reversed(comments):
            body = (c.get("message") or c.get("text") or "").strip()
            if body and not is_our_comment(body):
                user = c.get("user")
                if user:
                    return user.get("id")
                break
        return None
    except Exception as e:
        log.error("Error getting last comment user for issue %s: %s", issue_id, e)
        return None

async def jelly_get_request_user(tmdb_id: int) -> Optional[int]:
    """Get the user ID who originally requested the movie."""
    try:
        client = _client_lazy()
        
        # Get all requests to find the one with matching TMDB ID
        r = await client.get(f"{BASE}/api/v1/request", headers=_headers)
        r.raise_for_status()
        requests = r.json().get("results", [])
        
        for req in requests:
            if req.get("media", {}).get("tmdbId") == tmdb_id:
                requested_by = req.get("requestedBy")
                if requested_by:
                    return requested_by.get("id")
                break
        return None
    except Exception as e:
        log.error("Error getting request user for TMDB %s: %s", tmdb_id, e)
        return None

async def jelly_can_user_delete_movie(issue_id: int, tmdb_id: int) -> bool:
    """Check if the user commenting can delete the movie (must be the original requester)."""
    try:
        # Get the user who made the last comment
        comment_user_id = await jelly_get_last_comment_user(issue_id)
        if not comment_user_id:
            log.warning("Could not determine commenting user for issue %s", issue_id)
            return False
        
        # Get the user who originally requested the movie
        request_user_id = await jelly_get_request_user(tmdb_id)
        if not request_user_id:
            log.warning("Could not determine original requester for TMDB %s", tmdb_id)
            return False
        
        # Check if they match
        can_delete = comment_user_id == request_user_id
        log.info("Permission check: comment_user=%s, request_user=%s, can_delete=%s", 
                 comment_user_id, request_user_id, can_delete)
        
        return can_delete
    except Exception as e:
        log.error("Error checking delete permission for issue %s, TMDB %s: %s", issue_id, tmdb_id, e)
        return False

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

async def jelly_delete_media_item(tmdb_id: int) -> bool:
    """Delete/remove a media item from Jellyseerr."""
    if not (BASE and API_KEY):
        log.warning("Cannot delete media item: missing Jellyseerr config")
        return False
        
    try:
        client = _client_lazy()
        
        # Get the media item using the movie ID endpoint
        r = await client.get(f"{BASE}/api/v1/movie/{tmdb_id}", headers=_headers, params={
            "language": "en"
        })
        r.raise_for_status()
        search_results = r.json().get("results", [])
        log.info("Jellyseerr search for TMDB %s returned %d results", tmdb_id, len(search_results))
        
        # Find the matching media item
        media_id = None
        for item in search_results:
            if item.get("id") == tmdb_id and item.get("mediaType") == "movie":
                # Get the media info to find the internal media ID
                media_info = item.get("mediaInfo")
                if media_info:
                    media_id = media_info.get("id")
                    break
        
        if not media_id:
            # Try alternative approach - get all media and find by TMDB ID
            try:
                r2 = await client.get(f"{BASE}/api/v1/media", headers=_headers)
                if r2.status_code == 200:
                    all_media = r2.json().get("results", [])
                    for media in all_media:
                        if media.get("tmdbId") == tmdb_id:
                            media_id = media.get("id")
                            break
            except Exception as e:
                log.info("Alternative media search failed: %s", e)
        
        if not media_id:
            log.info("No Jellyseerr media found for TMDB ID %s", tmdb_id)
            return False
            
        log.info("Found Jellyseerr media ID %s for TMDB %s", media_id, tmdb_id)
        
        # Delete the media item (this removes it from Jellyseerr)
        delete_r = await client.delete(f"{BASE}/api/v1/media/{media_id}", headers=_headers)
        if delete_r.status_code in (200, 202, 204):
            log.info("Successfully deleted Jellyseerr media %s for TMDB %s", media_id, tmdb_id)
            return True
        else:
            log.warning("Failed to delete Jellyseerr media %s: status %s", media_id, delete_r.status_code)
            return False
            
    except Exception as e:
        log.error("Error deleting media item for TMDB %s: %s", tmdb_id, e)
        return False