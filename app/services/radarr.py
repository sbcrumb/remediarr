from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import cfg
from app.logging import log


def _base() -> str:
    return cfg.RADARR_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {
        "X-Api-Key": cfg.RADARR_API_KEY,
        "Content-Type": "application/json",
    }


def _timeout() -> float:
    try:
        return float(cfg.RADARR_HTTP_TIMEOUT or 60)
    except Exception:
        return 60.0


# --------- lookups ---------

async def get_movie_by_tmdb(tmdb_id: int | None) -> Optional[Dict[str, Any]]:
    if not tmdb_id:
        return None
    url = f"{_base()}/api/v3/movie"
    params = {"tmdbId": tmdb_id}
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        return None


async def get_movie_by_imdb(imdb_id: str | None) -> Optional[Dict[str, Any]]:
    if not imdb_id:
        return None
    url = f"{_base()}/api/v3/movie"
    params = {"imdbId": imdb_id}
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        return None


# --------- files ---------

async def get_moviefiles(movie_id: int) -> List[Dict[str, Any]]:
    url = f"{_base()}/api/v3/moviefile"
    params = {"movieId": movie_id}
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


async def delete_moviefiles(movie_id: int) -> int:
    """
    Delete all existing movie files for the given movie ID.
    Returns the number of deleted files.
    """
    files = await get_moviefiles(movie_id)
    deleted = 0
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        for f in files:
            fid = f.get("id")
            if not fid:
                continue
            url = f"{_base()}/api/v3/moviefile/{fid}"
            try:
                r = await c.delete(url, headers=_headers())
                if r.status_code in (200, 202, 204):
                    deleted += 1
                else:
                    log.info("Radarr DELETE moviefile %s -> %s %s", fid, r.status_code, r.text[:200])
            except Exception as e:
                log.info("Radarr DELETE moviefile %s error: %s", fid, e)
    return deleted


# --------- actions / verification ---------

async def search_movie(movie_id: int) -> bool:
    """
    Trigger a Radarr MoviesSearch command.
    IMPORTANT: payload must be 'movieIds' (plural).
    """
    url = f"{_base()}/api/v3/command"
    payload = {"name": "MoviesSearch", "movieIds": [int(movie_id)]}
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        try:
            r = await c.post(url, headers=_headers(), json=payload)
            if r.status_code in (200, 201, 202):
                return True
            log.info("Radarr POST /command failed %s: %s", r.status_code, r.text[:250])
        except httpx.HTTPStatusError as e:
            log.info("Radarr POST /command HTTP error: %s", e)
        except Exception as e:
            log.info("Radarr POST /command error: %s", e)
    return False


async def queue_has_movie(movie_id: int) -> bool:
    """
    Check if the Radarr queue currently contains this movie.
    """
    url = f"{_base()}/api/v3/queue"
    params = {
        "page": 1,
        "pageSize": 50,
        "sortDirection": "ascending",
        # Radarr uses 'includeUnknownMovieItems'; if unknown, ignore silently
        "includeUnknownMovieItems": True,
    }
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        try:
            r = await c.get(url, headers=_headers(), params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.info("Radarr GET /queue error: %s", e)
            return False

    items = data.get("records") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for it in items or []:
        mid = it.get("movieId") or (it.get("movie") or {}).get("id")
        if mid == movie_id:
            return True
    return False


async def history_has_recent_grab(movie_id: int, seconds: int) -> bool:
    """
    Best-effort; Radarr history params vary by version. We'll be lenient:
    - Try history/movie first
    - If it fails, just return False (queue check is enough).
    """
    base = _base()
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        for url, params in [
            (f"{base}/api/v3/history/movie", {"movieId": movie_id, "page": 1, "pageSize": 20, "sortDirection": "descending"}),
            (f"{base}/api/v3/history", {"movieId": movie_id, "page": 1, "pageSize": 20, "sortDirection": "descending"}),
        ]:
            try:
                r = await c.get(url, headers=_headers(), params=params)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()
                records = data.get("records") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                # Consider any very recent "grabbed" or "downloadPending" event a success
                for rec in records or []:
                    et = (rec.get("eventType") or "").lower()
                    if et in ("grabbed", "downloadpending"):
                        return True
                return False
            except Exception:
                # Don't spam errors here; just fall back to False
                return False
    return False
