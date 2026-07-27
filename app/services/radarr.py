import os
import logging
from typing import Any, Dict, Optional
import httpx

log = logging.getLogger("remediarr")

BASE = os.getenv("RADARR_URL", "").rstrip("/")
API = f"{BASE}/api/v3"
KEY = os.getenv("RADARR_API_KEY", "")
HEADERS = {"X-Api-Key": KEY} if KEY else {}
TIMEOUT = int(os.getenv("RADARR_HTTP_TIMEOUT", "60"))

_client: Optional[httpx.AsyncClient] = None
def _client_lazy() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client

async def get_movie_by_tmdb(tmdb: int) -> Optional[Dict[str, Any]]:
    r = await _client_lazy().get(f"{API}/movie", headers=HEADERS, params={"tmdbId": tmdb})
    r.raise_for_status()
    items = r.json() or []
    return items[0] if items else None

async def delete_moviefiles(movie_id: int) -> int:
    # list files
    r = await _client_lazy().get(f"{API}/moviefile", headers=HEADERS, params={"movieId": movie_id})
    r.raise_for_status()
    files = r.json() or []
    removed = 0
    for f in files:
        fid = f.get("id")
        if not fid:
            continue
        dr = await _client_lazy().delete(f"{API}/moviefile/{fid}", headers=HEADERS)
        if dr.status_code in (200, 202, 204):
            removed += 1
    log.info("Movie %s delete_moviefiles: removed=%s", movie_id, removed)
    return removed

async def trigger_search_movie(movie_id: int) -> None:
    body = {"name": "MoviesSearch", "movieIds": [movie_id]}
    r = await _client_lazy().post(f"{API}/command", headers=HEADERS, json=body)
    r.raise_for_status()

