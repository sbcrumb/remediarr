from __future__ import annotations

from typing import Tuple

import httpx

from app.config import cfg
from app.logging import log


async def _ping_json(url: str, headers: dict) -> Tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=headers)
            ok = r.status_code == 200
            return ok, f"{r.status_code}"
    except Exception as e:
        return False, str(e)


async def sonarr_ok() -> Tuple[bool, str]:
    url = cfg.SONARR_URL.rstrip("/") + "/api/v3/system/status"
    headers = {"X-Api-Key": cfg.SONARR_API_KEY}
    ok, detail = await _ping_json(url, headers)
    if not ok:
        log.warning("Sonarr health check failed: %s", detail)
    return ok, detail


async def radarr_ok() -> Tuple[bool, str]:
    url = cfg.RADARR_URL.rstrip("/") + "/api/v3/system/status"
    headers = {"X-Api-Key": cfg.RADARR_API_KEY}
    ok, detail = await _ping_json(url, headers)
    if not ok:
        log.warning("Radarr health check failed: %s", detail)
    return ok, detail
