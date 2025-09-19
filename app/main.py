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
    s_ok, s_detail = await sonarr_ok()
    r_ok, r_detail = await radarr_ok()
    b_ok, b_detail = await bazarr_ok()
    
    overall_ok = s_ok and r_ok and b_ok
    
    return {
        "status": "ok" if overall_ok else "degraded",
        "services": {
            "sonarr": {"status": "ok" if s_ok else "error", "detail": s_detail},
            "radarr": {"status": "ok" if r_ok else "error", "detail": r_detail},
            "bazarr": {"status": "ok" if b_ok else "error", "detail": b_detail}
        }
    }


@app.on_event("startup")
async def on_startup():
    log.info("%s v%s starting on %s:%s", cfg.APP_NAME, cfg.VERSION, cfg.APP_HOST, cfg.APP_PORT)
    s_ok, s_detail = await sonarr_ok()
    r_ok, r_detail = await radarr_ok()
    b_ok, b_detail = await bazarr_ok()
    msg = "\n".join([
        f"{cfg.APP_NAME} v{cfg.VERSION} started.",
        f"Sonarr health: {'OK' if s_ok else 'FAIL'} ({s_detail})",
        f"Radarr health: {'OK' if r_ok else 'FAIL'} ({r_detail})",
        f"Bazarr health: {'OK' if b_ok else 'FAIL'} ({b_detail})",
    ])
    log.info(msg)
    await notify(title=f"{cfg.APP_NAME} started", message=msg)