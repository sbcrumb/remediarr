import os
import logging
from typing import Any, Dict, List, Optional
import httpx
from datetime import datetime, timezone

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

async def get_movie_by_imdb(imdb: str) -> Optional[Dict[str, Any]]:
    r = await _client_lazy().get(f"{API}/movie", headers=HEADERS, params={"imdbId": imdb})
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

async def delete_movie(movie_id: int, delete_files: bool = True) -> bool:
    """Delete a movie from Radarr entirely."""
    try:
        params = {"deleteFiles": "true" if delete_files else "false", "addImportExclusion": "false"}
        r = await _client_lazy().delete(f"{API}/movie/{movie_id}", headers=HEADERS, params=params)
        if r.status_code in (200, 202, 204):
            log.info("Movie %s deleted from Radarr (deleteFiles=%s)", movie_id, delete_files)
            return True
        else:
            log.warning("Failed to delete movie %s from Radarr: status %s", movie_id, r.status_code)
            return False
    except Exception as e:
        log.error("Error deleting movie %s from Radarr: %s", movie_id, e)
        return False

def _parse_history_listish(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rec = data.get("records")
        if isinstance(rec, list):
            return rec
    return []

def _to_dt(s: str) -> Optional[datetime]:
    try:
        # Radarr dates are ISO8601; ensure tz-aware for comparisons
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

async def latest_grab_timestamp(movie_id: int) -> Optional[datetime]:
    # Try v3 segmented endpoint first
    client = _client_lazy()
    urls = [
        f"{API}/history/movie?movieId={movie_id}&page=1&pageSize=20&sortDirection=descending",
        f"{API}/history?movieId={movie_id}&page=1&pageSize=20&sortDirection=descending",
    ]
    for url in urls:
        r = await client.get(url, headers=HEADERS)
        if r.status_code >= 400:
            log.info("Radarr GET %s failed: %s", url.replace(API, "/api/v3"), r.status_code)
            continue
        items = _parse_history_listish(r.json())
        for ev in items:
            if (ev.get("eventType") or "").lower() == "grabbed":
                dt = _to_dt(ev.get("date") or "")
                if dt:
                    return dt
    return None

async def has_new_grab_since(movie_id: int, baseline: Optional[datetime]) -> bool:
    client = _client_lazy()
    urls = [
        f"{API}/history/movie?movieId={movie_id}&page=1&pageSize=20&sortDirection=descending",
        f"{API}/history?movieId={movie_id}&page=1&pageSize=20&sortDirection=descending",
    ]
    for url in urls:
        r = await client.get(url, headers=HEADERS)
        if r.status_code >= 400:
            continue
        for ev in _parse_history_listish(r.json()):
            if (ev.get("eventType") or "").lower() == "grabbed":
                ev_dt = _to_dt(ev.get("date") or "")
                if ev_dt and (baseline is None or ev_dt > baseline):
                    return True
    return False
