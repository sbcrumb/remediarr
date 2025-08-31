from __future__ import annotations

from typing import Optional

import httpx
import apprise

from app.config import cfg
from app.logging import log


async def send_gotify(title: str, message: str, priority: Optional[int] = None) -> None:
    if not (cfg.GOTIFY_URL and cfg.GOTIFY_TOKEN):
        return
    try:
        data = {
            "title": title,
            "message": message,
            "priority": priority if priority is not None else cfg.GOTIFY_PRIORITY,
        }
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{cfg.GOTIFY_URL.rstrip('/')}/message?token={cfg.GOTIFY_TOKEN}",
                json=data,
            )
            r.raise_for_status()
    except Exception as e:
        log.info("Gotify send failed: %s", e)


async def send_apprise(title: str, message: str) -> None:
    if not cfg.APPRISE_URLS:
        return
    try:
        a = apprise.Apprise()
        for url in [u.strip() for u in cfg.APPRISE_URLS.split(";") if u.strip()]:
            a.add(url)
        a.notify(title=title, body=message)
    except Exception as e:
        log.info("Apprise send failed: %s", e)


async def notify(title: str, message: str) -> None:
    await send_gotify(title, message)
    await send_apprise(title, message)
