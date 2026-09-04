"""Shared history/blocklist helpers for the Radarr and Sonarr history APIs,
which are identical in shape. Each arr service module supplies its own
httpx client, API base, and headers; these functions hold the shared,
arr-agnostic logic so a fix to one doesn't have to be re-applied by hand to
the other (that's already happened once in this repo)."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

import httpx

log = logging.getLogger("remediarr")

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def to_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def parse_history_listish(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    return []


def newest_first(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(events, key=lambda e: to_dt(e.get("date") or "") or _EPOCH, reverse=True)


def download_id(ev: Dict[str, Any]) -> str:
    return str(ev.get("downloadId") or (ev.get("data") or {}).get("downloadId") or "")


async def fetch_history(
    client: httpx.AsyncClient, api_base: str, headers: dict,
    urls: List[str], id_field: str, id_value: int, arr_name: str,
) -> List[Dict[str, Any]]:
    """Try each URL in turn; drop records for other series/movies (older
    *arr builds ignore the id filter on the generic /history endpoint and
    return global history — without this a foreign release could get
    blocklisted)."""
    for url in urls:
        try:
            r = await client.get(url, headers=headers)
        except Exception as e:
            log.info("%s history request error: %s", arr_name, e)
            continue
        if r.status_code >= 400:
            log.info("%s GET %s failed: %s", arr_name, url.replace(api_base, ""), r.status_code)
            continue
        items = [
            ev for ev in parse_history_listish(r.json())
            if ev.get(id_field) in (None, id_value)
        ]
        if items:
            return newest_first(items)
    return []


async def mark_history_failed(
    client: httpx.AsyncClient, api_base: str, headers: dict, history_id: int, arr_name: str,
) -> bool:
    """The 'Mark as Failed' action — blocklists the release behind that
    history record. Note: this is the same endpoint the *arr UI's button
    uses, and *arr may itself trigger its own replacement search as a side
    effect of this call, independent of whatever search Remediarr triggers
    afterward. We don't try to suppress that (no clean API for it) — the
    blocklist happening first means both searches draw from the same
    reduced candidate pool, so at worst it's a redundant grab, not a repeat
    of the original bad release."""
    for url in (f"{api_base}/history/failed/{history_id}", f"{api_base}/history/failed?id={history_id}"):
        try:
            r = await client.post(url, headers=headers, json={})
        except Exception as e:
            log.info("%s mark-failed request error: %s", arr_name, e)
            continue
        if r.status_code in (200, 201, 202, 204):
            return True
        log.info("%s POST %s failed: %s", arr_name, url.replace(api_base, ""), r.status_code)
    return False
