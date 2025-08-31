# app/main.py
# Remediarr FastAPI entrypoint:
# - Logs version on boot
# - ALWAYS pings Radarr & Sonarr at startup (logs results)
# - Optionally notifies via Gotify/Apprise if configured
# - Mounts the Jellyseerr webhook router
# - GET / returns health + version

import os
import logging
from typing import Tuple, Dict, Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# IMPORTANT: use package-qualified import
from app.webhooks.router import router as jellyseerr_router  # POST /webhook/jellyseerr

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] [%(levelname)s] %(message)s")
log = logging.getLogger("remediarr")

# ---------------- App & env ----------------
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8189"))

# Radarr
RADARR_URL = (os.getenv("RADARR_URL", "http://radarr:7878") or "").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "") or ""
RADARR_HTTP_TIMEOUT = float(os.getenv("RADARR_HTTP_TIMEOUT", "60"))

# Sonarr
SONARR_URL = (os.getenv("SONARR_URL", "http://sonarr:8989") or "").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "") or ""
SONARR_HTTP_TIMEOUT = float(os.getenv("SONARR_HTTP_TIMEOUT", "60"))

# Notifiers (optional)
GOTIFY_URL = (os.getenv("GOTIFY_URL", "") or "").rstrip("/")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "") or ""
GOTIFY_PRIORITY = int(os.getenv("GOTIFY_PRIORITY", "5"))

APPRISE_URL = (os.getenv("APPRISE_URL", "") or "").rstrip("/")
APPRISE_TARGETS = [u.strip() for u in (os.getenv("APPRISE_TARGETS", "") or "").split(",") if u.strip()]

app = FastAPI(title="Remediarr")


def _read_version() -> str:
    """
    Resolve version from env (APP_VERSION or VERSION) or VERSION file fallback.
    """
    v = os.getenv("APP_VERSION") or os.getenv("VERSION")
    if v:
        return v.strip()
    for p in ("./VERSION", "/app/VERSION"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "0.0.0-dev"


async def _notify(title: str, message: str) -> None:
    """
    Fire-and-forget: Gotify and/or Apprise-server if configured.
    """
    # Gotify
    if GOTIFY_URL and GOTIFY_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(
                    f"{GOTIFY_URL}/message",
                    params={"token": GOTIFY_TOKEN},
                    json={"title": title, "message": message, "priority": GOTIFY_PRIORITY},
                )
        except Exception as e:
            log.info("Gotify send failed: %s", e)

    # Apprise-server
    if APPRISE_URL and APPRISE_TARGETS:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(
                    f"{APPRISE_URL}/notify",
                    json={"title": title, "body": message, "type": "info", "urls": APPRISE_TARGETS},
                )
        except Exception as e:
            log.info("Apprise send failed: %s", e)


async def _ping_radarr() -> Tuple[bool, str]:
    if not (RADARR_URL and RADARR_API_KEY):
        return False, "Radarr not configured"
    try:
        async with httpx.AsyncClient(timeout=RADARR_HTTP_TIMEOUT) as c:
            r = await c.get(f"{RADARR_URL}/api/v3/system/status", params={"apikey": RADARR_API_KEY})
            r.raise_for_status()
            data: Dict[str, Any] = r.json() if isinstance(r.json(), dict) else {}
            name = data.get("instanceName") or "Radarr"
            ver = data.get("version") or "?"
            return True, f"{name} {ver}"
    except Exception as e:
        return False, f"Radarr error: {e}"


async def _ping_sonarr() -> Tuple[bool, str]:
    if not (SONARR_URL and SONARR_API_KEY):
        return False, "Sonarr not configured"
    try:
        async with httpx.AsyncClient(timeout=SONARR_HTTP_TIMEOUT) as c:
            r = await c.get(f"{SONARR_URL}/api/v3/system/status", params={"apikey": SONARR_API_KEY})
            r.raise_for_status()
            data: Dict[str, Any] = r.json() if isinstance(r.json(), dict) else {}
            name = data.get("instanceName") or "Sonarr"
            ver = data.get("version") or "?"
            return True, f"{name} {ver}"
    except Exception as e:
        return False, f"Sonarr error: {e}"


@app.on_event("startup")
async def _on_startup():
    version = _read_version()
    log.info("===========================================")
    log.info(" Remediarr starting — version: %s", version)
    log.info("===========================================")

    # ALWAYS run health checks (log results)
    r_ok, r_msg = await _ping_radarr()
    s_ok, s_msg = await _ping_sonarr()
    log.info("Healthcheck: Radarr -> %s", r_msg)
    log.info("Healthcheck: Sonarr -> %s", s_msg)

    # Notifications are OPTIONAL (only if configured)
    if r_ok and s_ok:
        await _notify("Remediarr up", f"{version} ready. Radarr OK ({r_msg}); Sonarr OK ({s_msg}).")
    else:
        await _notify(
            "Remediarr startup check",
            f"{version} with issues:\n  Radarr: {r_msg}\n  Sonarr: {s_msg}",
        )


@app.get("/", include_in_schema=False)
async def health() -> JSONResponse:
    """
    Simple health: shows version only.
    """
    return JSONResponse({"ok": True, "service": "remediarr", "version": _read_version()})


# Mount webhook routes (e.g., POST /webhook/jellyseerr)
app.include_router(jellyseerr_router)
