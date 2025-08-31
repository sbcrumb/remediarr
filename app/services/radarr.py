from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import httpx

from app.config import cfg
from app.logging import log


def _base() -> str:
    return cfg.RADARR_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"X-Api-Key": cfg.RADARR_API_KEY}


async def _get_json(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=cfg.RADARR_HTTP_TIMEOUT) as c:
            r = await c.get(url, headers=_headers(), params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.info("Radarr GET %s failed: %s", path, e)
        return None


async def _post_json(path: str, json: Dict[str, Any]) -> Optional[Any]:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=cfg.RADARR_HTTP_TIMEOUT) as c:
            r = await c.post(url, headers=_headers(), json=json)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.info("Radarr POST %s failed: %s", path, e)
        return None


async def _delete(path: str) -> bool:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=cfg.RADARR_HTTP_TIMEOUT) as c:
            r = await c.delete(url, headers=_headers())
            return r.status_code in (200, 202, 204)
    except Exception as e:
        log.info("Radarr DELETE %s failed: %s", path, e)
        return False


async def _get_all_movies() -> List[Dict[str, Any]]:
    data = await _get_json("/api/v3/movie")
    return data if isinstance(data, list) else []


async def get_movie_by_tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    items = await _get_all_movies()
    for m in items:
        if int(m.get("tmdbId") or 0) == int(tmdb_id):
            return m
    return None


async def get_movie_by_imdb(imdb_id: str) -> Optional[Dict[str, Any]]:
    items = await _get_all_movies()
    for m in items:
        prov = (m.get("imdbId") or "") or (m.get("foreignId") or "")
        if isinstance(prov, str) and prov.lower().replace("imdb:", "") == imdb_id.lower().replace("imdb:", ""):
            return m
    return None


async def list_moviefiles(movie_id: int) -> List[int]:
    files = await _get_json("/api/v3/moviefile", params={"movieId": movie_id})
    if not isinstance(files, list):
        return []
    return [int(f["id"]) for f in files if f.get("id")]


async def delete_moviefiles(movie_id: int) -> int:
    ids = await list_moviefiles(movie_id)
    deleted = 0
    for fid in ids:
        if await _delete(f"/api/v3/moviefile/{fid}"):
            deleted += 1
    return deleted


async def search_movie(movie_id: int) -> bool:
    # Radarr accepts "MoviesSearch" with movieIds OR "MovieSearch" with movieId (both work on recent builds)
    cmd = {"name": "MovieSearch", "movieId": movie_id}
    return bool(await _post_json("/api/v3/command", cmd))


async def queue_has_movie(movie_id: int) -> bool:
    q = await _get_json("/api/v3/queue", params={"pageSize": 50})
    items = q.get("records") if isinstance(q, dict) else (q if isinstance(q, list) else [])
    for it in items:
        if int(it.get("movieId") or 0) == int(movie_id):
            return True
    return False


async def history_has_recent_grab(movie_id: int, window_seconds: int) -> bool:
    params = {"eventType": "grabbed", "movieId": movie_id, "pageSize": 20, "page": 1}
    hist = await _get_json("/api/v3/history", params=params)
    now = datetime.now(timezone.utc)
    items = []
    if isinstance(hist, dict):
        items = hist.get("records") or hist.get("results") or []
    elif isinstance(hist, list):
        items = hist
    for h in items:
        ds = h.get("date") or h.get("eventDate") or h.get("updated")
        if not ds:
            continue
        try:
            ts = datetime.fromisoformat(ds.replace("Z", "+00:00"))
        except Exception:
            continue
        if now - ts <= timedelta(seconds=window_seconds):
            return True
    return False
