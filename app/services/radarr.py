from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import cfg
from app.logging import log


def _base() -> str:
    return cfg.RADARR_URL.rstrip("/") + "/api/v3"


def _headers() -> Dict[str, str]:
    return {"X-Api-Key": cfg.RADARR_API_KEY, "Content-Type": "application/json"}


def _timeout() -> float:
    try:
        return float(getattr(cfg, "RADARR_HTTP_TIMEOUT", 60))
    except Exception:
        return 60.0


def _iso_to_dt(s: str | None):
    if not s or not isinstance(s, str):
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None


# ---------- lookups ----------

async def get_movie_by_tmdb(tmdb_id: int | None) -> Optional[Dict[str, Any]]:
    if not tmdb_id:
        return None
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/movie", headers=_headers())
        r.raise_for_status()
        for m in r.json():
            if int(m.get("tmdbId") or 0) == int(tmdb_id):
                return m
    return None


async def get_movie_by_imdb(imdb_id: str | None) -> Optional[Dict[str, Any]]:
    if not imdb_id:
        return None
    t = imdb_id.lower()
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/movie", headers=_headers())
        r.raise_for_status()
        for m in r.json():
            if (m.get("imdbId") or "").lower() == t:
                return m
    return None


async def get_movie_by_title(title: str | None) -> Optional[Dict[str, Any]]:
    if not title:
        return None
    t = title.strip().lower()
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/movie", headers=_headers())
        r.raise_for_status()
        for m in r.json():
            if (m.get("title") or "").strip().lower() == t:
                return m
    return None


# ---------- mutations ----------

async def delete_moviefiles(movie_id: int) -> int:
    count = 0
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/moviefile", headers=_headers(), params={"movieId": int(movie_id)})
        r.raise_for_status()
        for f in r.json() or []:
            fid = f.get("id")
            if fid is None:
                continue
            dr = await c.delete(f"{_base()}/moviefile/{int(fid)}", headers=_headers())
            dr.raise_for_status()
            count += 1
    log.info("Radarr: deleted %s file(s) for movieId=%s", count, movie_id)
    return count


async def search_movie(movie_id: int) -> bool:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        payload = {"name": "MoviesSearch", "movieIds": [int(movie_id)]}
        r = await c.post(f"{_base()}/command", headers=_headers(), json=payload)
        if r.status_code >= 400:
            log.info("Radarr MoviesSearch failed (%s). Falling back to MovieSearch…", r.status_code)
            payload = {"name": "MovieSearch", "movieId": int(movie_id)}
            r = await c.post(f"{_base()}/command", headers=_headers(), json=payload)
        r.raise_for_status()
        log.info("Radarr: search command accepted for movieId=%s (status=%s)", movie_id, r.status_code)
        return True


# ---------- verification ----------

async def queue_has_movie(movie_id: int) -> bool:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        params = {"page": 1, "pageSize": 50, "sortDirection": "ascending", "includeUnknownMovieItems": "true"}
        r = await c.get(f"{_base()}/queue", headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        items = (data.get("records") or data.get("results") or data.get("items") or []) if isinstance(data, dict) else (data or [])
        for it in items:
            mid = it.get("movieId") or (it.get("movie") or {}).get("id")
            if mid and int(mid) == int(movie_id):
                return True
    return False


async def history_has_recent_grab(movie_id: int, window_sec: int) -> bool:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        params = {"movieId": int(movie_id), "page": 1, "pageSize": 50, "sortDirection": "descending"}
        r = await c.get(f"{_base()}/history/movie", headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        records = (data.get("records") or data.get("results") or data.get("items") or []) if isinstance(data, dict) else (data or [])
        now = datetime.now(timezone.utc)
        for rec in records:
            if (rec.get("eventType") or "").lower() != "grabbed":
                continue
            ts = _iso_to_dt(rec.get("date") or rec.get("created"))
            if not ts:
                continue
            age = (now - ts).total_seconds()
            if 0 <= age <= int(window_sec):
                return True
    return False
