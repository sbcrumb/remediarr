import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("remediarr")

SONARR_URL = os.getenv("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")
SONARR_HTTP_TIMEOUT = int(os.getenv("SONARR_HTTP_TIMEOUT", "60"))

def _headers() -> Dict[str, str]:
    return {"X-Api-Key": SONARR_API_KEY} if SONARR_API_KEY else {}

def _ts_from_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None

async def get_series_by_tvdb(tvdb_id: int) -> Optional[Dict[str, Any]]:
    if not tvdb_id:
        return None
    async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/series",
                        params={"tvdbId": tvdb_id},
                        headers=_headers())
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list) and items:
            return items[0]
        return None

async def get_series_by_tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    if not tmdb_id:
        return None
    async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/series",
                        params={"tmdbId": tmdb_id},
                        headers=_headers())
        r.raise_for_status()
        items = r.json()
        if isinstance(items, list) and items:
            return items[0]
        return None

async def get_episode(series_id: int, season: int, episode: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/episode",
                        params={"seriesId": series_id},
                        headers=_headers())
        r.raise_for_status()
        eps = r.json() or []
        for ep in eps:
            if ep.get("seasonNumber") == season and ep.get("episodeNumber") == episode:
                return ep
        return None

async def delete_episode_files(series_id: int, episode_ids: List[int]) -> int:
    """Delete files for the provided episode ids. Returns count deleted files."""
    if not episode_ids:
        return 0
    deleted = 0
    async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/episodefile",
                        params={"seriesId": series_id},
                        headers=_headers())
        r.raise_for_status()
        files = r.json() or []
        ids_to_delete: List[int] = []
        for f in files:
            if f.get("episodeId") in episode_ids:
                fid = f.get("id")
                if fid:
                    ids_to_delete.append(fid)
        for fid in ids_to_delete:
            try:
                rr = await c.delete(f"{SONARR_URL}/api/v3/episodefile/{fid}", headers=_headers())
                rr.raise_for_status()
                deleted += 1
            except httpx.HTTPStatusError as e:
                log.info("Sonarr failed to delete episodefile %s: %s", fid, e)
    return deleted

async def trigger_episode_search(episode_ids: List[int]) -> None:
    if not episode_ids:
        return
    payload = {"name": "EpisodeSearch", "episodeIds": episode_ids}
    async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
        r = await c.post(f"{SONARR_URL}/api/v3/command",
                         json=payload, headers=_headers())
        r.raise_for_status()

async def latest_grab_timestamp(series_id: int) -> Optional[datetime]:
    """Timestamp of latest grabbed for the series (handles v3/v4)."""
    async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
        # Preferred in newer Sonarr
        r = await c.get(f"{SONARR_URL}/api/v3/history/series",
                        params={"seriesId": series_id, "page": 1, "pageSize": 50, "sortDirection": "descending"},
                        headers=_headers())
        if r.status_code == 404:
            # Fallback for older builds
            r = await c.get(f"{SONARR_URL}/api/v3/history",
                            params={"seriesId": series_id, "page": 1, "pageSize": 50, "sortDirection": "descending"},
                            headers=_headers())
        r.raise_for_status()
        data = r.json()
        records = data.get("records") if isinstance(data, dict) else (data or [])
        latest = None
        for rec in records:
            if str(rec.get("eventType","")).lower() != "grabbed":
                continue
            ts = _ts_from_iso(rec.get("date"))
            if ts and (not latest or ts > latest):
                latest = ts
        return latest
