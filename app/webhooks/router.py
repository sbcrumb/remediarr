from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Header, Request, HTTPException

from app.config import cfg
from app.logging import log
from app.webhooks.handlers import handle_jellyseerr

router = APIRouter()


def _eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a, b)
    except Exception:
        return False


def _verify_shared_secret(raw: bytes, sig_header: str | None) -> None:
    # Enforce ONLY if a non-empty secret is configured
    secret = (cfg.WEBHOOK_SHARED_SECRET or "").strip()
    if not secret:
        return

    if not sig_header or not sig_header.startswith("sha256="):
        log.warning("Webhook auth failed: missing/invalid X-Jellyseerr-Signature")
        raise HTTPException(status_code=401, detail="Missing/invalid signature")

    provided = sig_header.split("sha256=", 1)[1].strip()
    computed = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    if not _eq(provided, computed):
        log.warning("Webhook auth failed: signature mismatch")
        raise HTTPException(status_code=401, detail="Signature mismatch")


def _verify_header(req: Request) -> None:
    # Enforce ONLY if BOTH name AND value are non-empty
    name = (cfg.WEBHOOK_HEADER_NAME or "").strip()
    value = (cfg.WEBHOOK_HEADER_VALUE or "").strip()
    if not name or not value:
        return

    got = req.headers.get(name, "").strip()
    if got != value:
        log.warning("Webhook auth failed: header %s mismatch (got=%r)", name, got)
        raise HTTPException(status_code=401, detail="Header check failed")


@router.post("/webhook/jellyseerr")
async def jellyseerr_webhook(
    req: Request,
    x_jellyseerr_signature: str | None = Header(default=None, convert_underscores=False),
):
    raw = await req.body()

    # Apply checks (only when actually configured)
    _verify_header(req)
    _verify_shared_secret(raw, x_jellyseerr_signature)

    payload = await req.json()
    return await handle_jellyseerr(payload)