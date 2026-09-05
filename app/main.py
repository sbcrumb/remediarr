from __future__ import annotations

from fastapi import FastAPI

from app.config import cfg
from app.logging import log
from app.services.health import sonarr_ok, radarr_ok, bazarr_ok
from app.services.notify import notify
from app.webhooks.router import router as jellyseerr_router


app = FastAPI(title=cfg.APP_NAME, version=cfg.VERSION)
app.include_router(jellyseerr_router)


@app.get("/")
async def root():
    return {"app": cfg.APP_NAME, "version": cfg.VERSION, "ok": True}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/detailed")
async def health_detailed():
    s_results = await sonarr_ok()
    r_results = await radarr_ok()
    b_ok, b_detail = await bazarr_ok()

    overall_ok = all(ok for ok, _ in s_results.values()) and all(ok for ok, _ in r_results.values()) and b_ok

    services = {
        name: {"status": "ok" if ok else "error", "detail": detail}
        for name, (ok, detail) in {**s_results, **r_results}.items()
    }
    services["bazarr"] = {"status": "ok" if b_ok else "error", "detail": b_detail}

    return {
        "status": "ok" if overall_ok else "degraded",
        "services": services,
    }


@app.on_event("startup")
async def on_startup():
    log.info("%s v%s starting on %s:%s", cfg.APP_NAME, cfg.VERSION, cfg.APP_HOST, cfg.APP_PORT)
    s_results = await sonarr_ok()
    r_results = await radarr_ok()
    b_ok, b_detail = await bazarr_ok()
    lines = [f"{cfg.APP_NAME} v{cfg.VERSION} started."]
    for name, (ok, detail) in {**s_results, **r_results}.items():
        lines.append(f"{name.capitalize()} health: {'OK' if ok else 'FAIL'} ({detail})")
    lines.append(f"Bazarr health: {'OK' if b_ok else 'FAIL'} ({b_detail})")
    msg = "\n".join(lines)
    log.info(msg)
    if not cfg.DISABLE_STARTUP_NOTIFICATION:
        await notify(title=f"{cfg.APP_NAME} started", message=msg)