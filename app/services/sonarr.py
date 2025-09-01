from __future__ import annotations

import time
from typing import Iterable, List, Optional

import httpx

from app.config import cfg
from app.logging import log


def _client() -> httpx.AsyncClient:
    headers = {"X-Api-Key": cfg.SONARR_API_KEY} if cfg.SONARR_API_KEY else {}
    base = (cfg.SONARR_URL or "").rstrip("/")
    return httpx.AsyncClient(base_url=base, headers=headers, timeout=float(cfg.SONARR_HTTP_TIMEOUT or 60))


# --- Lookups ---

async def get_series_by_tvdb(tvdb_id: int) -> Optional[dict]:
    async with _client() as c:
        r = await c.get("/api/v3/series", params={"tvdbId": tvdb_id})
        r.raise_for_status()
        arr = r.json()
        return arr[0] if arr else None


async def find_episode_ids(series_id: int, season: int, episode: int) -> List[int]:
    async with _client() as c:
        # fetch all eps for series, then filter S/E
        r = await c.get("/api/v3/episode", params={"seriesId": series_id})
        r.raise_for_status()
        eps = r.json() or []
        ids = [e["id"] for e in eps if int(e.get("seasonNumber", -1)) == int(season) and int(e.get("episodeNumber", -1)) == int(episode)]
        return ids


# --- Actions ---

async def delete_episodefiles_by_episode_ids(series_id: int, ep_ids: Iterable[int]) -> int:
    ep_ids = list(ep_ids)
    if not ep_ids:
        return 0
    async with _client() as c:
        r = await c.get("/api/v3/episodefile", params={"seriesId": series_id})
        r.raise_for_status()
        files = r.json() or []
        to_del = [f["id"] for f in files if int(f.get("episodeId", -1)) in ep_ids]
        removed = 0
        for fid in to_del:
            rr = await c.delete(f"/api/v3/episodefile/{fid}")
            rr.raise_for_status()
            removed += 1
        log.info("Series %s delete_episodefiles: removed=%s", series_id, removed)
        return removed


async def search_episode_ids(ep_ids: Iterable[int]) -> None:
    ids = list(ep_ids)
    if not ids:
        return
    async with _client() as c:
        r = await c.post("/api/v3/command", json={"name": "EpisodeSearch", "episodeIds": ids})
        r.raise_for_status()


# --- Verification helpers ---

async def queue_has_any_of_episode_ids(ep_ids: Iterable[int]) -> bool:
    ids = set(int(x) for x in ep_ids)
    async with _client() as c:
        r = await c.get("/api/v3/queue", params={"page": 1, "pageSize": 50, "sortDirection": "ascending", "includeUnknownSeriesItems": True})
        r.raise_for_status()
        items = (r.json() or {}).get("records") or r.json() or []
        for it in items:
            ep = it.get("episode") or {}
            eid = ep.get("id") or it.get("episodeId")
            if eid is not None and int(eid) in ids:
                return True
    return False
