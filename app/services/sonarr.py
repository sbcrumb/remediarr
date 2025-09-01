from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.config import cfg
from app.logging import log


def _base() -> str:
    return cfg.SONARR_URL.rstrip("/") + "/api/v3"


def _headers() -> Dict[str, str]:
    return {"X-Api-Key": cfg.SONARR_API_KEY, "Content-Type": "application/json"}


def _timeout() -> float:
    try:
        return float(getattr(cfg, "SONARR_HTTP_TIMEOUT", 60))
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

async def get_series_by_tvdb(tvdb_id: int | None) -> Optional[Dict[str, Any]]:
    if not tvdb_id:
        return None
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/series", headers=_headers(), params={"tvdbId": int(tvdb_id)})
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else None


async def get_series_by_title(title: str | None) -> Optional[Dict[str, Any]]:
    if not title:
        return None
    t = title.strip().lower()
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/series", headers=_headers())
        r.raise_for_status()
        for s in r.json() or []:
            if (s.get("title") or "").strip().lower() == t:
                return s
    return None


# ---------- episodes & files ----------

async def list_episodes(series_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/episode", headers=_headers(), params={"seriesId": int(series_id)})
        r.raise_for_status()
        return r.json() or []


async def find_episode_ids(series_id: int, season: Optional[int], episode: Optional[int]) -> List[int]:
    """
    Return Sonarr episode ids matching season/episode.
    Only returns ids when BOTH season and episode are supplied.
    """
    if season is None or episode is None:
        return []
    eps = await list_episodes(series_id)
    out: List[int] = []
    for e in eps:
        if int(e.get("seasonNumber") or -1) == int(season) and int(e.get("episodeNumber") or -1) == int(episode):
            if e.get("id") is not None:
                out.append(int(e["id"]))
    return out


async def delete_episodefiles_by_episode_ids(series_id: int, episode_ids: Sequence[int]) -> int:
    """
    Delete episode files for the given Sonarr episode ids (exact match).
    """
    if not episode_ids:
        log.info("Sonarr: no episode ids provided; nothing to delete.")
        return 0
    want = set(int(x) for x in episode_ids)
    count = 0
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.get(f"{_base()}/episodefile", headers=_headers(), params={"seriesId": int(series_id)})
        r.raise_for_status()
        for f in r.json() or []:
            eid = f.get("episodeId")
            fid = f.get("id")
            if eid is None or fid is None:
                continue
            if int(eid) in want:
                dr = await c.delete(f"{_base()}/episodefile/{int(fid)}", headers=_headers())
                dr.raise_for_status()
                count += 1
    log.info("Sonarr: deleted %s file(s) for seriesId=%s (episodes=%s)", count, series_id, sorted(list(want)))
    return count


# ---------- commands ----------

async def search_episode_ids(episode_ids: Sequence[int]) -> bool:
    """
    Trigger Sonarr EpisodeSearch for the given episode ids ONLY.
    """
    if not episode_ids:
        return False
    payload = {"name": "EpisodeSearch", "episodeIds": [int(x) for x in episode_ids]}
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        r = await c.post(f"{_base()}/command", headers=_headers(), json=payload)
        r.raise_for_status()
        log.info("Sonarr: EpisodeSearch accepted for episodeIds=%s", list(episode_ids))
        return True


# ---------- verification ----------

async def queue_has_any_of_episode_ids(episode_ids: Sequence[int]) -> bool:
    """
    True if Sonarr queue contains any of these episode ids.
    """
    if not episode_ids:
        return False
    want = set(int(x) for x in episode_ids)
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        params = {"page": 1, "pageSize": 50, "sortDirection": "ascending", "includeUnknownSeriesItems": "true"}
        r = await c.get(f"{_base()}/queue", headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        items = (data.get("records") or data.get("results") or data.get("items") or []) if isinstance(data, dict) else (data or [])
        for it in items:
            eid = it.get("episodeId") or (it.get("episode") or {}).get("id")
            if eid is not None and int(eid) in want:
                return True
    return False


async def history_has_recent_grab_for_episode_ids(series_id: int, episode_ids: Sequence[int], window_sec: int) -> bool:
    """
    True if a 'grabbed' history event exists for ANY of these episode ids within window_sec.
    """
    if not episode_ids:
        return False
    want = set(int(x) for x in episode_ids)
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        params = {"seriesId": int(series_id), "page": 1, "pageSize": 50, "sortDirection": "descending"}
        r = await c.get(f"{_base()}/history/series", headers=_headers(), params=params)
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
            if age < 0 or age > int(window_sec):
                continue
            eid = rec.get("episodeId")
            if eid is not None and int(eid) in want:
                return True
    return False
