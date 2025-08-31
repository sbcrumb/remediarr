import httpx
import asyncio
from app.logging import log

HTTP_MAX_RETRIES = 3
HTTP_RETRY_BACKOFF = 2.0

async def retry_http(callable_async, *, what: str):
    last_exc = None
    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            return await callable_async()
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.HTTPError) as e:
            last_exc = e
            wait = HTTP_RETRY_BACKOFF * (2 ** (attempt - 1))
            log.warning("HTTP error on %s (attempt %s/%s): %s. Retrying in %.1fs",
                        what, attempt, HTTP_MAX_RETRIES, e, wait)
            await asyncio.sleep(wait)
    raise last_exc
