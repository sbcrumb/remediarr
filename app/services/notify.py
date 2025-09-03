from __future__ import annotations

import asyncio
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
            log.info("Gotify notification sent successfully")
    except Exception as e:
        log.warning("Gotify send failed: %s", e)


async def send_apprise(title: str, message: str) -> None:
    if not cfg.APPRISE_URLS:
        return
    try:
        # Run the blocking apprise calls in a thread pool to avoid blocking the event loop
        def _send_apprise_sync():
            a = apprise.Apprise()
            for url in [u.strip() for u in cfg.APPRISE_URLS.split(";") if u.strip()]:
                a.add(url)
            return a.notify(title=title, body=message)
        
        # Execute in thread pool to prevent blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _send_apprise_sync)
        
        if result:
            log.info("Apprise notification sent successfully")
        else:
            log.warning("Apprise notification failed to send")
            
    except Exception as e:
        log.warning("Apprise send failed: %s", e)


async def notify(title: str, message: str) -> None:
    await send_gotify(title, message)
    await send_apprise(title, message)