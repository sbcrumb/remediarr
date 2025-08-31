import asyncio
import httpx
from typing import Any, Dict, List, Optional, Tuple

from app.config import RADARR_URL, RADARR_API_KEY
from app.http import retry_http


def _params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = {"apikey": RADARR_API_KEY}
    if extra:
        base.update(extra)
    return base


async def get_system_status() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/system/status", params=_params())
        r.raise_for_status()
        return r.json()


async def get_movie_by_tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/movie", params=_params({"tmdbId": tmdb_id}))
        r.raise_for_status()
        items = r.json()
        return items[0] if isinstance(items, list) and items else None


async def list_movie_files(movie_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/moviefile", params=_params({"movieId": movie_id}))
        r.raise_for_status()
        return r.json()


async def delete_movie_file(movie_file_id: int) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        async def _do():
            return await c.delete(f"{RADARR_URL}/api/v3/moviefile/{movie_file_id}", params=_params())
        await retry_http(_do, what=f"radarr delete moviefile {movie_file_id}")


async def get_movie_history(movie_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/history/movie", params=_params({"movieId": movie_id}))
        r.raise_for_status()
        return r.json()


async def mark_history_failed(history_id: int) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        async def _do():
            return await c.post(f"{RADARR_URL}/api/v3/history/failed/{history_id}", params=_params())
        await retry_http(_do, what=f"radarr mark failed {history_id}")


async def search_movie(movie_id: int) -> None:
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    async with httpx.AsyncClient(timeout=30) as c:
        async def _do():
            return await c.post(f"{RADARR_URL}/api/v3/command", params=_params(), json=payload)
        await retry_http(_do, what=f"radarr MoviesSearch movieId={movie_id}")


async def queue_contains_movie(movie_id: int) -> bool:
    """Check if Radarr queue currently has this movie."""
    async with httpx.AsyncClient(timeout=20) as c:
        # Radarr queue can be paged; keep it simple and fetch first page with large size
        r = await c.get(
            f"{RADARR_URL}/api/v3/queue",
            params=_params({"page": 1, "pageSize": 100, "includeUnknownSeriesItems": "true"}),
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("records") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return False
        for it in items:
            if int(it.get("movieId", -1)) == int(movie_id):
                return True
        return False


async def wait_until_movie_queued(movie_id: int, timeout_sec: int = 25, poll_sec: float = 2.0) -> bool:
    """Poll Radarr queue briefly to see if a search resulted in something queued."""
    total = 0.0
    while total < timeout_sec:
        if await queue_contains_movie(movie_id):
            return True
        await asyncio.sleep(poll_sec)
        total += poll_sec
    return False


async def fail_last_grab_delete_files_and_search(movie_id: int) -> Tuple[int, bool]:
    """
    Mark last grab failed (if present), delete all files, trigger a search.
    Returns (deleted_count, queued_now)
    """
    hist = await get_movie_history(movie_id)
    grabbed = next((h for h in hist if str(h.get("eventType", "")).lower() == "grabbed"), None)
    if grabbed:
        try:
            await mark_history_failed(int(grabbed["id"]))
        except Exception:
            pass

    files = await list_movie_files(movie_id)
    deleted = 0
    for f in files:
        try:
            await delete_movie_file(int(f["id"]))
            deleted += 1
        except Exception:
            # ignore individual delete failures
            pass

    await search_movie(movie_id)
    queued = await wait_until_movie_queued(movie_id)
    return deleted, queued
