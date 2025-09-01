import os
import logging
from datetime import datetime
from typing import Optional, Tuple, List

import httpx

log = logging.getLogger("remediarr")

RADARR_URL = os.getenv("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")
RADARR_HTTP_TIMEOUT = float(os.getenv("RADARR_HTTP_TIMEOUT", "60"))

HEADERS = {"X-Api-Key": RADARR_API_KEY}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=RADARR_URL, headers=HEADERS, timeout=RADARR_HTTP_TIMEOUT)


async def get_movie_by_tmdb(tmdb_id: int) -> Optional[dict]:
    """Return the Radarr movie (dict) by TMDb ID, or None."""
    async with _client() as c:
        r = await c.get("/api/v3/movie", params={"tmdbId": tmdb_id})
        r.raise_for_status()
        data = r.json()
        # Radarr returns a single object (dict) or a list depending on version; normalize to dict or None
        if isinstance(data, list):
            return data[0] if data else None
        return data or None


async def get_movie_by_imdb(imdb_id: str) -> Optional[dict]:
    """Return the Radarr movie (dict) by IMDb ID, or None."""
    async with _client() as c:
        r = await c.get("/api/v3/movie", params={"imdbId": imdb_id})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data[0] if data else None
        return data or None


async def delete_movie_files(movie_id: int) -> int:
    """Delete all movie files for the given movie and return count removed."""
    removed = 0
    async with _client() as c:
        r = await c.get("/api/v3/moviefile", params={"movieId": movie_id})
        r.raise_for_status()
        files = r.json() or []
        for f in files:
            fid = f.get("id")
            if not fid:
                continue
            dr = await c.delete(f"/api/v3/moviefile/{fid}")
            dr.raise_for_status()
            removed += 1
    log.info("Movie %s delete_moviefiles: removed=%s", movie_id, removed)
    return removed


async def trigger_movie_search(movie_id: int) -> None:
    """Kick off a Radarr movie search for the given movie id."""
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    async with _client() as c:
        r = await c.post("/api/v3/command", json=payload)
        r.raise_for_status()


async def queue_has_movie(movie_id: int) -> bool:
    """Check Radarr queue for a pending/grabbed item of this movie."""
    async with _client() as c:
        r = await c.get(
            "/api/v3/queue",
            params={
                "page": 1,
                "pageSize": 50,
                "sortDirection": "ascending",
                "includeUnknownMovieItems": "true",
            },
        )
        r.raise_for_status()
        data = r.json() or {}
        items = data.get("records") or data.get("records", []) or data if isinstance(data, list) else []
        for it in items:
            # Radarr v3 queue items often have "movieId"; some have nested "movie": {"id": ...}
            if it.get("movieId") == movie_id:
                return True
            movie = it.get("movie") or {}
            if movie.get("id") == movie_id:
                return True
    return False


async def latest_grab_timestamp(movie_id: int) -> Optional[datetime]:
    """Return the latest 'grabbed' event timestamp for a movie (client-side filtered)."""
    async with _client() as c:
        r = await c.get(
            "/api/v3/history/movie",
            params={
                "movieId": movie_id,
                "page": 1,
                "pageSize": 20,
                "sortDirection": "descending",
            },
        )
        r.raise_for_status()
        data = r.json() or {}
        records = data.get("records") or data if isinstance(data, list) else []
        for rec in records:
            if (rec.get("eventType") or "").lower() == "grabbed":
                # 'date' or 'eventDate' depending on version
                ts = rec.get("date") or rec.get("eventDate")
                if ts:
                    try:
                        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        pass
        return None


async def wait_for_new_grab_or_queue(movie_id: int, baseline: Optional[datetime], total_sec: int, poll_sec: int) -> bool:
    """
    After triggering search, poll queue/history until a new grab appears or item is in queue.
    """
    deadline = datetime.utcnow().timestamp() + total_sec
    while datetime.utcnow().timestamp() < deadline:
        if await queue_has_movie(movie_id):
            return True
        latest = await latest_grab_timestamp(movie_id)
        if latest and (not baseline or latest > baseline):
            return True
        await asyncio.sleep(poll_sec)
    return False
