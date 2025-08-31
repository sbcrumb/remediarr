import asyncio
import httpx
from typing import Any, Dict, List, Optional, Tuple

from app.config import SONARR_URL, SONARR_API_KEY
from app.http import retry_http


def _params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = {"apikey": SONARR_API_KEY}
    if extra:
        base.update(extra)
    return base


async def get_system_status() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/system/status", params=_params())
        r.raise_for_status()
        return r.json()


async def get_series_by_tvdb(tvdb_id: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/series", params=_params({"tvdbId": tvdb_id}))
        r.raise_for_status()
        items = r.json()
        return items[0] if isinstance(items, list) and items else None


async def find_episode(series_id: int, season: int, episode: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{SONARR_URL}/api/v3/episode",
            params=_params({"seriesId": series_id, "seasonNumber": season}),
        )
        r.raise_for_status()
        for ep in r.json():
            if int(ep.get("episodeNumber", -1)) == int(episode):
                return ep
        return None


async def delete_episode_file(episode_file_id: int) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        async def _do():
            return await c.delete(f"{SONARR_URL}/api/v3/episodefile/{episode_file_id}", params=_params())
        await retry_http(_do, what=f"sonarr delete episodefile {episode_file_id}")


async def episode_search(episode_id: int) -> None:
    payload = {"name": "EpisodeSearch", "episodeIds": [episode_id]}
    async with httpx.AsyncClient(timeout=30) as c:
        async def _do():
            return await c.post(f"{SONARR_URL}/api/v3/command", params=_params(), json=payload)
        await retry_http(_do, what=f"sonarr EpisodeSearch episodeId={episode_id}")


async def queue_contains_episode(episode_id: int) -> bool:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/queue", params=_params({"page": 1, "pageSize": 100}))
        r.raise_for_status()
        data = r.json()
        items = data.get("records") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return False
        for it in items:
            # Sonarr queue items have episodeId inside nested "episode" or direct key (depends on version)
            ep = it.get("episode") or {}
            if int(ep.get("id", it.get("episodeId", -1))) == int(episode_id):
                return True
        return False


async def wait_until_episode_queued(episode_id: int, timeout_sec: int = 25, poll_sec: float = 2.0) -> bool:
    total = 0.0
    while total < timeout_sec:
        if await queue_contains_episode(episode_id):
            return True
        await asyncio.sleep(poll_sec)
        total += poll_sec
    return False


async def delete_and_search_episode(series_id: int, season: int, episode: int) -> Tuple[bool, bool, Optional[int]]:
    """
    If the episode has a file, delete it; trigger a search.
    Returns (deleted_file, queued_now, episode_id)
    """
    ep = await find_episode(series_id, season, episode)
    if not ep:
        return False, False, None

    deleted = False
    ep_file_id = ep.get("episodeFileId")
    if ep_file_id:
        try:
            await delete_episode_file(int(ep_file_id))
            deleted = True
        except Exception:
            pass

    await episode_search(int(ep["id"]))
    queued = await wait_until_episode_queued(int(ep["id"]))
    return deleted, queued, int(ep["id"])