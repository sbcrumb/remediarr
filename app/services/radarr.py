import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("remediarr")

RADARR_URL = os.getenv("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")
RADARR_HTTP_TIMEOUT = int(os.getenv("RADARR_HTTP_TIMEOUT", "60"))

def _headers() -> Dict[str, str]:
    return {"X-Api-Key": RADARR_API_KEY} if RADARR_API_KEY else {}

def _ts_from_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        # tolerate trailing Z
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        try:
            # some builds: 2025-09-01T01:43:16.7490000Z
            if value.endswith("Z"):
                value = value[:-1]
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except Exception:
            return None

async def get_movie_by_tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    if not tmdb_id:
        return None
    async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/movie",
                        params={"tmdbId": tmdb_id},
                        headers=_headers())
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list) and items:
            return items[0]
        return None

async def get_movie_by_imdb(imdb_id: str) -> Optional[Dict[str, Any]]:
    if not imdb_id:
        return None
    async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/movie",
                        params={"imdbId": imdb_id},
                        headers=_headers())
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list) and items:
            return items[0]
        return None

async def delete_movie_files(movie_id: int) -> int:
    """Delete all movie files. Returns count deleted."""
    if not movie_id:
        return 0
    deleted = 0
    async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/moviefile",
                        params={"movieId": movie_id},
                        headers=_headers())
        r.raise_for_status()
        files = r.json() or []
        for f in files:
            fid = f.get("id")
            if not fid:
                continue
            try:
                rr = await c.delete(f"{RADARR_URL}/api/v3/moviefile/{fid}",
                                    headers=_headers())
                rr.raise_for_status()
                deleted += 1
            except httpx.HTTPStatusError as e:
                log.info("Radarr failed to delete moviefile %s: %s", fid, e)
    return deleted

async def trigger_search(movie_id: int) -> None:
    if not movie_id:
        return
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
        r = await c.post(f"{RADARR_URL}/api/v3/command",
                         json=payload, headers=_headers())
        r.raise_for_status()

async def get_movie_history(movie_id: int) -> List[Dict[str, Any]]:
    """Handles both dict-with-records and plain list (older Radarr)."""
    async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/history/movie",
                        params={"movieId": movie_id, "page": 1, "pageSize": 50, "sortDirection": "descending"},
                        headers=_headers())
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("records", []) or []
        if isinstance(data, list):
            return data
        return []

async def latest_grab_timestamp(movie_id: int) -> Optional[datetime]:
    records = await get_movie_history(movie_id)
    latest: Optional[datetime] = None
    for rec in records:
        if str(rec.get("eventType", "")).lower() != "grabbed":
            continue
        ts = _ts_from_iso(rec.get("date"))
        if ts and (not latest or ts > latest):
            latest = ts
    return latest

async def queue_has_movie(movie_id: int) -> bool:
    async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/queue",
                        params={"page": 1, "pageSize": 50, "sortDirection": "ascending",
                                "includeUnknownMovieItems": "true"},
                        headers=_headers())
        r.raise_for_status()
        data = r.json()
        items = data.get("records") if isinstance(data, dict) else (data or [])
        for item in items or []:
            mid = item.get("movieId") or (item.get("movie") or {}).get("id")
            if mid == movie_id:
                return True
        return False
