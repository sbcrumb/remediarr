import logging
from typing import Any, Dict, List, Optional
import httpx

from app.services import arr_history as H
from app.services import arr_instances as I

log = logging.getLogger("remediarr")

_INSTANCES = I.load_instances("SONARR")


def _instance(instance: int = 0) -> I.ArrInstance:
    inst = I.get_instance(_INSTANCES, instance)
    if inst is None:
        raise ValueError(
            f"Sonarr instance {instance} is not configured "
            f"(set SONARR_URL_{instance}/SONARR_API_KEY_{instance})."
        )
    return inst


def instances() -> Dict[int, I.ArrInstance]:
    """All configured Sonarr instances, keyed by index. Public accessor for
    callers outside this module (e.g. health checks) that need to iterate
    every configured instance rather than resolve a single one."""
    return _INSTANCES


_client: Optional[httpx.AsyncClient] = None
def _client_lazy() -> httpx.AsyncClient:
    # Shared across all instances — base URL/headers are passed per-request
    # (never baked into the client), so one client can safely serve multiple
    # Sonarr instances. Timeout uses instance 0's; per-instance timeouts
    # aren't supported yet (not worth the complexity until something needs it).
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_instance(0).timeout)
    return _client

async def get_series_by_tvdb(tvdb: int, instance: int = 0) -> Optional[Dict[str, Any]]:
    inst = _instance(instance)
    r = await _client_lazy().get(f"{inst.base}/series", headers=inst.headers, params={"tvdbId": tvdb})
    r.raise_for_status()
    items = r.json() or []
    return items[0] if items else None

async def list_episodes(series_id: int, instance: int = 0) -> List[Dict[str, Any]]:
    inst = _instance(instance)
    r = await _client_lazy().get(f"{inst.base}/episode", headers=inst.headers, params={"seriesId": series_id})
    r.raise_for_status()
    return r.json() or []

async def episode_ids_for(series_id: int, season: int, episode: int, instance: int = 0) -> List[int]:
    eps = await list_episodes(series_id, instance)
    ids: List[int] = []
    for e in eps:
        if e.get("seasonNumber") == season and e.get("episodeNumber") == episode:
            if isinstance(e.get("id"), int):
                ids.append(e["id"])
    return ids

async def get_all_episode_ids_for_season(series_id: int, season: int, instance: int = 0) -> List[int]:
    eps = await list_episodes(series_id, instance)
    return [e["id"] for e in eps if e.get("seasonNumber") == season and isinstance(e.get("id"), int)]

async def delete_episodefiles(series_id: int, episode_ids: List[int], instance: int = 0) -> int:
    inst = _instance(instance)
    eps = await list_episodes(series_id, instance)
    file_ids: List[int] = []
    by_id = {e["id"]: e for e in eps if "id" in e}
    for eid in episode_ids:
        efid = (by_id.get(eid) or {}).get("episodeFileId")
        if efid:
            file_ids.append(efid)
    removed = 0
    for fid in file_ids:
        dr = await _client_lazy().delete(f"{inst.base}/episodefile/{fid}", headers=inst.headers)
        if dr.status_code in (200, 202, 204):
            removed += 1
    log.info("Series %s delete_episodefiles: removed=%s", series_id, removed)
    return removed

async def trigger_episode_search(episode_ids: List[int], instance: int = 0) -> None:
    if not episode_ids:
        return
    inst = _instance(instance)
    body = {"name": "EpisodeSearch", "episodeIds": episode_ids}
    r = await _client_lazy().post(f"{inst.base}/command", headers=inst.headers, json=body)
    r.raise_for_status()

async def delete_all_episodefiles_for_season(series_id: int, season: int, instance: int = 0) -> int:
    inst = _instance(instance)
    eps = await list_episodes(series_id, instance)
    file_ids = [
        e["episodeFileId"] for e in eps
        if e.get("seasonNumber") == season and e.get("episodeFileId")
    ]
    removed = 0
    for fid in file_ids:
        dr = await _client_lazy().delete(f"{inst.base}/episodefile/{fid}", headers=inst.headers)
        if dr.status_code in (200, 202, 204):
            removed += 1
    log.info("Series %s season %s delete_all_episodefiles: removed=%s", series_id, season, removed)
    return removed

async def trigger_season_search(series_id: int, season: int, instance: int = 0) -> None:
    inst = _instance(instance)
    body = {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season}
    r = await _client_lazy().post(f"{inst.base}/command", headers=inst.headers, json=body)
    r.raise_for_status()

async def get_seasons_with_files(series_id: int, instance: int = 0) -> set[int]:
    """Return the set of season numbers (excluding season 0/specials) that
    have at least one episode file on disk. One Sonarr fetch; callers derive
    both "how many" and "which one" from the same result instead of each
    re-fetching the episode list."""
    eps = await list_episodes(series_id, instance)
    seasons: set[int] = set()
    for e in eps:
        sn = e.get("seasonNumber")
        if isinstance(sn, int) and sn > 0 and e.get("hasFile"):
            seasons.add(sn)
    return seasons


def _episode_id(ev: Dict[str, Any]) -> Optional[int]:
    eid = ev.get("episodeId")
    if not isinstance(eid, int):
        eid = (ev.get("data") or {}).get("episodeId")
    try:
        return int(eid)
    except (TypeError, ValueError):
        return None


async def _history_for_series(series_id: int, instance: int = 0) -> List[Dict[str, Any]]:
    inst = _instance(instance)
    return await H.fetch_history(
        _client_lazy(), inst.base, inst.headers,
        urls=[
            f"{inst.base}/history/series?seriesId={series_id}",
            f"{inst.base}/history?seriesId={series_id}&page=1&pageSize=200&sortDirection=descending",
        ],
        id_field="seriesId", id_value=series_id, arr_name="Sonarr",
    )


async def blocklist_current_releases(series_id: int, episode_ids: List[int], instance: int = 0) -> int:
    """
    Blocklist the release(s) that produced the episode files currently on disk, so a
    re-search cannot grab the exact same (broken) release again.

    Sonarr has no "add to blocklist" endpoint; the supported path is marking the
    'grabbed' history record as failed (what the UI's "Mark as Failed" button does),
    which blocklists that release. A season pack yields one grab shared by many
    episodes, so downloadIds are de-duplicated. Returns how many were blocklisted.
    """
    if not episode_ids:
        return 0
    inst = _instance(instance)
    events = await _history_for_series(series_id, instance)
    if not events:
        log.info("Series %s: no history to blocklist", series_id)
        return 0

    wanted = set(episode_ids)

    # Resolve each wanted episode INDEPENDENTLY to the downloadId of the release
    # currently on disk for it: the newest event (import or grab) for that
    # episode. An import is always newer than its own grab, so it naturally wins
    # when present; if the episode hasn't been imported yet (or was imported with
    # no downloadId, e.g. manually), the newest grab is the correct fallback.
    # Doing this per-episode — instead of one shared decision for the whole
    # batch — means one episode having (or lacking) an import record can't affect
    # any other episode's result. An episode whose newest event has no downloadId
    # is skipped rather than risking an older, superseded release's id.
    target_dls: set[str] = set()
    seen_eps: set[int] = set()
    for ev in events:  # newest first
        eid = _episode_id(ev)
        if eid is None or eid not in wanted or eid in seen_eps:
            continue
        etype = (ev.get("eventType") or "").lower()
        if etype not in ("downloadfolderimported", "grabbed"):
            continue
        seen_eps.add(eid)
        did = H.download_id(ev)
        if did:
            target_dls.add(did)

    if not target_dls:
        log.info("Series %s: no matching history with a downloadId for episodes %s", series_id, sorted(wanted))
        return 0

    # One 'grabbed' record per distinct downloadId (season packs share one
    # downloadId across many episodes — de-duplicated so it's only marked failed,
    # and only fires one re-download, once per release).
    targets: List[int] = []
    seen_dls: set[str] = set()
    for ev in events:
        if (ev.get("eventType") or "").lower() != "grabbed":
            continue
        hid = ev.get("id")
        if not isinstance(hid, int):
            continue
        did = H.download_id(ev)
        if did not in target_dls or did in seen_dls:
            continue
        seen_dls.add(did)
        targets.append(hid)
        if len(seen_dls) == len(target_dls):
            break

    blocked = 0
    for hid in targets:
        if await H.mark_history_failed(_client_lazy(), inst.base, inst.headers, hid, "Sonarr"):
            blocked += 1
    log.info("Series %s blocklist_current_releases: episodes=%s blocked=%s", series_id, episode_ids, blocked)
    return blocked
