import logging
from typing import Any, Dict, List, Optional
import httpx

from app.services import arr_history as H
from app.services import arr_instances as I

log = logging.getLogger("remediarr")

_INSTANCES = I.load_instances("RADARR")


def _instance(instance: int = 0) -> I.ArrInstance:
    inst = I.get_instance(_INSTANCES, instance)
    if inst is None:
        raise ValueError(
            f"Radarr instance {instance} is not configured "
            f"(set RADARR_URL_{instance}/RADARR_API_KEY_{instance})."
        )
    return inst


_client: Optional[httpx.AsyncClient] = None
def _client_lazy() -> httpx.AsyncClient:
    # Shared across all instances — see sonarr.py's _client_lazy for why this
    # is safe (base URL/headers are always passed per-request).
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_instance(0).timeout)
    return _client

async def get_movie_by_tmdb(tmdb: int, instance: int = 0) -> Optional[Dict[str, Any]]:
    inst = _instance(instance)
    r = await _client_lazy().get(f"{inst.base}/movie", headers=inst.headers, params={"tmdbId": tmdb})
    r.raise_for_status()
    items = r.json() or []
    return items[0] if items else None

async def delete_moviefiles(movie_id: int, instance: int = 0) -> int:
    inst = _instance(instance)
    # list files
    r = await _client_lazy().get(f"{inst.base}/moviefile", headers=inst.headers, params={"movieId": movie_id})
    r.raise_for_status()
    files = r.json() or []
    removed = 0
    for f in files:
        fid = f.get("id")
        if not fid:
            continue
        dr = await _client_lazy().delete(f"{inst.base}/moviefile/{fid}", headers=inst.headers)
        if dr.status_code in (200, 202, 204):
            removed += 1
    log.info("Movie %s delete_moviefiles: removed=%s", movie_id, removed)
    return removed

async def trigger_search_movie(movie_id: int, instance: int = 0) -> None:
    inst = _instance(instance)
    body = {"name": "MoviesSearch", "movieIds": [movie_id]}
    r = await _client_lazy().post(f"{inst.base}/command", headers=inst.headers, json=body)
    r.raise_for_status()

async def _history_for_movie(movie_id: int, instance: int = 0) -> List[Dict[str, Any]]:
    inst = _instance(instance)
    return await H.fetch_history(
        _client_lazy(), inst.base, inst.headers,
        urls=[
            f"{inst.base}/history/movie?movieId={movie_id}",
            f"{inst.base}/history?movieId={movie_id}&page=1&pageSize=100&sortDirection=descending",
        ],
        id_field="movieId", id_value=movie_id, arr_name="Radarr",
    )


async def blocklist_current_release(movie_id: int, instance: int = 0) -> int:
    """
    Blocklist the release that produced the movie file currently on disk, so a
    re-search cannot grab the exact same (broken) release again.

    Radarr has no "add to blocklist" endpoint; the supported path is marking the
    'grabbed' history record as failed (what the UI's "Mark as Failed" button does),
    which blocklists that release. Returns how many releases were blocklisted.
    """
    inst = _instance(instance)
    events = await _history_for_movie(movie_id, instance)
    if not events:
        log.info("Movie %s: no history to blocklist", movie_id)
        return 0

    # Find the single newest event (import or grab) for this movie — whichever
    # produced the file currently on disk. An import event is always newer than
    # its own grab, so this naturally prefers the import when one exists. If
    # THAT event has no downloadId (e.g. a manual import), we deliberately give
    # up rather than fall through to an OLDER event's downloadId, which could
    # belong to an already-superseded release.
    target_dl = ""
    for ev in events:  # newest first
        etype = (ev.get("eventType") or "").lower()
        if etype in ("downloadfolderimported", "grabbed"):
            target_dl = H.download_id(ev)
            break

    if not target_dl:
        log.info("Movie %s: newest history event has no downloadId to blocklist", movie_id)
        return 0

    target_hid: Optional[int] = None
    for ev in events:
        if (ev.get("eventType") or "").lower() != "grabbed":
            continue
        hid = ev.get("id")
        if isinstance(hid, int) and H.download_id(ev) == target_dl:
            target_hid = hid
            break

    if target_hid is None:
        log.info("Movie %s: no grabbed history record for downloadId %s", movie_id, target_dl)
        return 0

    blocked = 1 if await H.mark_history_failed(_client_lazy(), inst.base, inst.headers, target_hid, "Radarr") else 0
    log.info("Movie %s blocklist_current_release: blocked=%s", movie_id, blocked)
    return blocked
