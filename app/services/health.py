from __future__ import annotations

from typing import Tuple

import httpx

from app.config import cfg
from app.logging import log


import asyncio
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


async def _health_check_with_retry(service_name: str, check_func) -> Tuple[bool, str]:
    """
    Perform health check with configurable retries and delay.
    
    Args:
        service_name: Name of the service being checked (for logging)
        check_func: Async function that returns (bool, str) tuple
        
    Returns:
        Tuple of (success, detail_message)
    """
    retries = cfg.STARTUP_HEALTH_CHECK_RETRIES
    delay = cfg.STARTUP_HEALTH_CHECK_DELAY
    
    for attempt in range(1, retries + 1):
        log.info("Health check %s: attempt %d/%d", service_name, attempt, retries)
        
        ok, detail = await check_func()
        
        if ok:
            log.info("Health check %s: SUCCESS on attempt %d", service_name, attempt)
            return ok, detail
        
        log.warning("Health check %s: FAILED attempt %d/%d - %s", 
                   service_name, attempt, retries, detail)
        
        # Don't sleep after the last attempt
        if attempt < retries:
            log.info("Health check %s: waiting %ds before retry...", service_name, delay)
            await asyncio.sleep(delay)
    
    log.error("Health check %s: FAILED after %d attempts", service_name, retries)
    return False, f"Failed after {retries} attempts. Last error: {detail}"


async def sonarr_ok() -> Tuple[bool, str]:
    async def _check():
        url = cfg.SONARR_URL.rstrip("/") + "/api/v3/system/status"
        headers = {"X-Api-Key": cfg.SONARR_API_KEY}
        return await _ping_json(url, headers)
    
    return await _health_check_with_retry("Sonarr", _check)


async def radarr_ok() -> Tuple[bool, str]:
    async def _check():
        url = cfg.RADARR_URL.rstrip("/") + "/api/v3/system/status"
        headers = {"X-Api-Key": cfg.RADARR_API_KEY}
        return await _ping_json(url, headers)
    
    return await _health_check_with_retry("Radarr", _check)


async def bazarr_ok() -> Tuple[bool, str]:
    """Check Bazarr health. Returns (True, detail) if configured and healthy, or (True, 'disabled') if not configured."""
    if not cfg.BAZARR_URL or not cfg.BAZARR_API_KEY:
        return True, "disabled"
    
    async def _check():
        # Try system status endpoint, fallback to API root if needed
        url = cfg.BAZARR_URL.rstrip("/") + "/api/system/status"
        headers = {"X-API-KEY": cfg.BAZARR_API_KEY}
        
        # First try system/status
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url, headers=headers)
                if r.status_code == 200:
                    return True, f"{r.status_code}"
                elif r.status_code == 404:
                    # Try alternative endpoint structure
                    alt_url = cfg.BAZARR_URL.rstrip("/") + "/api"
                    r2 = await c.get(alt_url, headers=headers)
                    ok = r2.status_code in (200, 404)  # 404 is acceptable for API root
                    return ok, f"{r2.status_code}"
                else:
                    return False, f"{r.status_code}"
        except Exception as e:
            return False, str(e)
    
    return await _health_check_with_retry("Bazarr", _check)
