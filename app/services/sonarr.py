import os
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx
from datetime import datetime, timezone

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

def _parse_history_listish(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    return []

def _to_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

async def latest_grab_timestamp(series_id: int, episode_ids: List[int]) -> Optional[datetime]:
    client = _client_lazy()
    urls = [
        f"{API}/history/series?seriesId={series_id}&page=1&pageSize=50&sortDirection=descending",
        f"{API}/history?seriesId={series_id}&page=1&pageSize=50&sortDirection=descending",
    ]
    for url in urls:
        r = await client.get(url, headers=HEADERS)
        if r.status_code >= 400:
            log.info("Sonarr GET %s failed: %s", url.replace(API, "/api/v3"), r.status_code)
            continue
        for ev in _parse_history_listish(r.json()):
            if (ev.get("eventType") or "").lower() == "grabbed":
                data = ev.get("data") or {}
                eid = data.get("episodeId")
                dt = _to_dt(ev.get("date") or "")
                if dt and (not episode_ids or eid in episode_ids):
                    return dt
    return None

async def has_new_grab_since(series_id: int, episode_ids: List[int], baseline: Optional[datetime]) -> bool:
    client = _client_lazy()
    urls = [
        f"{API}/history/series?seriesId={series_id}&page=1&pageSize=50&sortDirection=descending",
        f"{API}/history?seriesId={series_id}&page=1&pageSize=50&sortDirection=descending",
        f"{API}/history?seriesId={series_id}&page=1&pageSize=50&sortDirection=descending",
    ]
    for url in urls:
        r = await client.get(url, headers=HEADERS)
        if r.status_code >= 400:
            continue
        for ev in _parse_history_listish(r.json()):
            if (ev.get("eventType") or "").lower() == "grabbed":
                data = ev.get("data") or {}
                eid = data.get("episodeId")
                dt = _to_dt(ev.get("date") or "")
                if dt and (baseline is None or dt > baseline) and (not episode_ids or eid in episode_ids):
                    return True
    return False
