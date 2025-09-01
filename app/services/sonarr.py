import os
import logging
import asyncio
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger("remediarr")

SONARR_URL = os.getenv("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")
SONARR_HTTP_TIMEOUT = float(os.getenv("SONARR_HTTP_TIMEOUT", "60"))

HEADERS = {"X-Api-Key": SONARR_API_KEY}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=SONARR_URL, headers=HEADERS, timeout=SONARR_HTTP_TIMEOUT)


async def get_series_by_tvdb(tvdb_id: int) -> Optional[dict]:
    async with _client() as c:
        r = await c.get("/api/v3/series", params={"tvdbId": tvdb_id})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data[0] if data else None
        return data or None


async def find_episode(series_id: int, season: int, episode: int) -> Optional[dict]:
    """Find a single episode object by season/episode numbers."""
    async with _client() as c:
        r = await c.get("/api/v3/episode", params={"seriesId": series_id, "seasonNumber": season})
        r.raise_for_status()
        eps = r.json() or []
        for e in eps:
            if e.get("seasonNumber") == season and e.get("episodeNumber") == episode:
                return e
        return None


async def delete_episode_file_for_episode(episode_id: int) -> int:
    """Delete file for a single episode if present. Returns 1 if a file was deleted, else 0."""
    async with _client() as c:
        r = await c.get(f"/api/v3/episode/{episode_id}")
        r.raise_for_status()
        ep = r.json() or {}
        file_id = ep.get("episodeFileId")
        if file_id:
            dr = await c.delete(f"/api/v3/episodefile/{file_id}")
            dr.raise_for_status()
            return 1
    return 0


async def trigger_episode_search(episode_id: int) -> None:
    """Run EpisodeSearch for the given episode id (episode-only search)."""
    async with _client() as c:
        payload = {"name": "EpisodeSearch", "episodeIds": [episode_id]}
        r = await c.post("/api/v3/command", json=payload)
        r.raise_for_status()


async def queue_has_episode(series_id: int, episode_id: int) -> bool:
    async with _client() as c:
        r = await c.get(
            "/api/v3/queue",
            params={
                "page": 1,
                "pageSize": 50,
                "sortDirection": "ascending",
                "includeUnknownSeriesItems": "true",
            },
        )
        r.raise_for_status()
        data = r.json() or {}
        items = data.get("records") or data if isinstance(data, list) else []
        for it in items:
            if it.get("seriesId") == series_id:
                # Some Sonarr items have "episode" or "episodeId", others have "episodeIds"
                if it.get("episodeId") == episode_id:
                    return True
                ids = it.get("episodeIds") or []
                if isinstance(ids, list) and episode_id in ids:
                    return True
                ep = it.get("episode") or {}
                if ep.get("id") == episode_id:
                    return True
        return False


async def latest_episode_grab(series_id: int, episode_id: int) -> Optional[datetime]:
    """Return latest grabbed timestamp for a specific episode in a series."""
    async with _client() as c:
        r = await c.get(
            "/api/v3/history/series",
            params={
                "seriesId": series_id,
                "page": 1,
                "pageSize": 25,
                "sortDirection": "descending",
            },
        )
        r.raise_for_status()
        data = r.json() or {}
        records = data.get("records") or data if isinstance(data, list) else []
        for rec in records:
            if (rec.get("eventType") or "").lower() == "grabbed":
                # Grab events include episodeId (single) or episodeIds (array)
                if rec.get("episodeId") == episode_id:
                    ts = rec.get("date") or rec.get("eventDate")
                    if ts:
                        try:
                            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except Exception:
                            pass
                ids = rec.get("episodeIds") or []
                if isinstance(ids, list) and episode_id in ids:
                    ts = rec.get("date") or rec.get("eventDate")
                    if ts:
                        try:
                            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except Exception:
                            pass
        return None


async def wait_for_episode_grab(series_id: int, episode_id: int, baseline: Optional[datetime], total_sec: int, poll_sec: int) -> bool:
    deadline = datetime.utcnow().timestamp() + total_sec
    while datetime.utcnow().timestamp() < deadline:
        if await queue_has_episode(series_id, episode_id):
            return True
        latest = await latest_episode_grab(series_id, episode_id)
        if latest and (not baseline or latest > baseline):
            return True
        await asyncio.sleep(poll_sec)
    return False
