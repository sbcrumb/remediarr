import httpx
from app.config import GOTIFY_URL, GOTIFY_TOKEN
from app.logging import log

async def notify(title: str, message: str, priority: int = 5):
    if not (GOTIFY_URL and GOTIFY_TOKEN):
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{GOTIFY_URL.rstrip('/')}/message",
                         params={"token": GOTIFY_TOKEN},
                         json={"title": title, "message": message, "priority": priority})
    except Exception as e:
        log.warning("Gotify send failed: %s", e)
