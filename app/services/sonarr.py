import os
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx

log = logging.getLogger("remediarr")

BASE = os.getenv("SONARR_URL", "").rstrip("/")
API = f"{BASE}/api/v3"
KEY = os.getenv("SONARR_API_KEY", "")
HEADERS = {"X-Api-Key": KEY} if KEY else {}
TIMEOUT = int(os.getenv("SONARR_HTTP_TIMEOUT", "60"))

_client: Optional[httpx.AsyncClient] = None
def _client_lazy() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client

async def get_series_by_tvdb(tvdb: int) -> Optional[Dict[str, Any]]:
    r = await _client_lazy().get(f"{API}/series", headers=HEADERS, params={"tvdbId": tvdb})
    r.raise_for_status()
    items = r.json() or []
    return items[0] if items else None

async def list_episodes(series_id: int) -> List[Dict[str, Any]]:
    r = await _client_lazy().get(f"{API}/episode", headers=HEADERS, params={"seriesId": series_id})
    r.raise_for_status()
    return r.json() or []

async def episode_ids_for(series_id: int, season: int, episode: int) -> List[int]:
    eps = await list_episodes(series_id)
    ids: List[int] = []
    for e in eps:
        if e.get("seasonNumber") == season and e.get("episodeNumber") == episode:
            if isinstance(e.get("id"), int):
                ids.append(e["id"])
    return ids

async def delete_episodefiles(series_id: int, episode_ids: List[int]) -> int:
    eps = await list_episodes(series_id)
    file_ids: List[int] = []
    by_id = {e["id"]: e for e in eps if "id" in e}
    for eid in episode_ids:
        efid = (by_id.get(eid) or {}).get("episodeFileId")
        if efid:
            file_ids.append(efid)
    removed = 0
    for fid in file_ids:
        dr = await _client_lazy().delete(f"{API}/episodefile/{fid}", headers=HEADERS)
        if dr.status_code in (200, 202, 204):
            removed += 1
    log.info("Series %s delete_episodefiles: removed=%s", series_id, removed)
    return removed

async def trigger_episode_search(episode_ids: List[int]) -> None:
    if not episode_ids:
        return
    body = {"name": "EpisodeSearch", "episodeIds": episode_ids}
    r = await _client_lazy().post(f"{API}/command", headers=HEADERS, json=body)
    r.raise_for_status()

async def delete_all_episodefiles_for_season(series_id: int, season: int) -> int:
    eps = await list_episodes(series_id)
    file_ids = [
        e["episodeFileId"] for e in eps
        if e.get("seasonNumber") == season and e.get("episodeFileId")
    ]
    removed = 0
    for fid in file_ids:
        dr = await _client_lazy().delete(f"{API}/episodefile/{fid}", headers=HEADERS)
        if dr.status_code in (200, 202, 204):
            removed += 1
    log.info("Series %s season %s delete_all_episodefiles: removed=%s", series_id, season, removed)
    return removed

async def trigger_season_search(series_id: int, season: int) -> None:
    body = {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season}
    r = await _client_lazy().post(f"{API}/command", headers=HEADERS, json=body)
    r.raise_for_status()

