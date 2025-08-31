from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import httpx

from app.config import cfg
from app.logging import log


def _base() -> str:
    return cfg.SONARR_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"X-Api-Key": cfg.SONARR_API_KEY}


async def _get_json(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=cfg.SONARR_HTTP_TIMEOUT) as c:
            r = await c.get(url, headers=_headers(), params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.info("Sonarr GET %s failed: %s", path, e)
        return None


async def _post_json(path: str, json: Dict[str, Any]) -> Optional[Any]:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=cfg.SONARR_HTTP_TIMEOUT) as c:
            r = await c.post(url, headers=_headers(), json=json)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.info("Sonarr POST %s failed: %s", path, e)
        return None


async def _delete(path: str) -> bool:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=cfg.SONARR_HTTP_TIMEOUT) as c:
            r = await c.delete(url, headers=_headers())
            return r.status_code in (200, 202, 204)
    except Exception as e:
        log.info("Sonarr DELETE %s failed: %s", path, e)
        return False


async def get_series_by_tvdb(tvdb_id: int) -> Optional[Dict[str, Any]]:
    data = await _get_json("/api/v3/series", params={"tvdbId": tvdb_id})
    if isinstance(data, list) and data:
        return data[0]
    return None


async def get_series_by_imdb(imdb_id: str) -> Optional[Dict[str, Any]]:
    lookup = await _get_json("/api/v3/series/lookup", params={"term": f"imdb:{imdb_id}"})
    tvdb = None
    if isinstance(lookup, list) and lookup:
        tvdb = lookup[0].get("tvdbId")
    if tvdb:
        return await get_series_by_tvdb(tvdb)
    return None


async def get_series_by_title(term: str) -> Optional[Dict[str, Any]]:
    lookup = await _get_json("/api/v3/series/lookup", params={"term": term})
    if isinstance(lookup, list) and lookup:
        tvdb = lookup[0].get("tvdbId")
        if tvdb:
            return await get_series_by_tvdb(tvdb)
    return None


async def find_episode_ids(series_id: int, season: Optional[int], episode: Optional[int]) -> List[int]:
    """Return episode IDs to act on. If season/episode missing, returns ALL episodes with files."""
    eps = await _get_json("/api/v3/episode", params={"seriesId": series_id})
    if not isinstance(eps, list):
        return []
    ids: List[int] = []
    for e in eps:
        if season is not None and e.get("seasonNumber") != season:
            continue
        if episode is not None and e.get("episodeNumber") != episode:
            continue
        # include only episodes that currently have a file
        if e.get("hasFile") and e.get("id"):
            ids.append(int(e["id"]))
    # If nothing specific selected but season/episode not provided, nuke all with files
    if not ids and season is None and episode is None:
        ids = [int(e["id"]) for e in eps if e.get("hasFile") and e.get("id")]
    return ids


async def delete_episodefiles_by_episode_ids(episode_ids: List[int]) -> int:
    """Delete episode files for given episode IDs (episodeFileId is on episode resource)."""
    if not episode_ids:
        return 0
    eps = await _get_json("/api/v3/episode", params={"episodeIds": ",".join(map(str, episode_ids))})
    if not isinstance(eps, list):
        return 0
    deleted = 0
    for e in eps:
        efid = e.get("episodeFileId")
        if efid:
            if await _delete(f"/api/v3/episodefile/{efid}"):
                deleted += 1
    return deleted


async def search_series(series_id: int) -> bool:
    cmd = {"name": "SeriesSearch", "seriesId": series_id}
    return bool(await _post_json("/api/v3/command", cmd))


async def queue_has_series(series_id: int) -> bool:
    q = await _get_json("/api/v3/queue", params={"pageSize": 50})
    if isinstance(q, dict):
        items = q.get("records") or q.get("results") or []
    else:
        items = q if isinstance(q, list) else []
    for it in items:
        if int(it.get("seriesId") or 0) == int(series_id):
            return True
    return False


async def history_has_recent_grab(series_id: int, window_seconds: int) -> bool:
    params = {"eventType": "grabbed", "seriesId": series_id, "pageSize": 20, "page": 1}
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