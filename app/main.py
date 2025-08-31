from __future__ import annotations

from fastapi import FastAPI

from app.config import cfg
from app.logging import log
from app.services.health import sonarr_ok, radarr_ok
from app.services.notify import notify
from app.webhooks.router import router as jellyseerr_router


app = FastAPI(title=cfg.APP_NAME, version=cfg.VERSION)
app.include_router(jellyseerr_router)


@app.get("/")
async def root():
    return {"app": cfg.APP_NAME, "version": cfg.VERSION, "ok": True}


@app.on_event("startup")
async def on_startup():
    log.info("%s v%s starting on %s:%s", cfg.APP_NAME, cfg.VERSION, cfg.APP_HOST, cfg.APP_PORT)
    s_ok, s_detail = await sonarr_ok()
    r_ok, r_detail = await radarr_ok()
    msg = "\n".join([
        f"{cfg.APP_NAME} v{cfg.VERSION} started.",
        f"Sonarr health: {'OK' if s_ok else 'FAIL'} ({s_detail})",
        f"Radarr health: {'OK' if r_ok else 'FAIL'} ({r_detail})",
    ])
    log.info(msg)
    await notify(title=f"{cfg.APP_NAME} started", message=msg)
