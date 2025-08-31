from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Header, Request, HTTPException

from app.config import cfg
from app.webhooks.handlers import handle_jellyseerr

router = APIRouter()


def _eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a, b)
    except Exception:
        return False


def _verify_shared_secret(raw: bytes, sig_header: str | None) -> None:
    if not cfg.WEBHOOK_SHARED_SECRET:
        return
    if not sig_header or not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing/invalid signature")
    provided = sig_header.split("sha256=", 1)[1].strip()
    computed = hmac.new(cfg.WEBHOOK_SHARED_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not _eq(provided, computed):
        raise HTTPException(status_code=401, detail="Signature mismatch")


def _verify_header(req: Request) -> None:
    if not cfg.WEBHOOK_HEADER_NAME:
        return
    expected = (cfg.WEBHOOK_HEADER_VALUE or "").strip()
    got = req.headers.get(cfg.WEBHOOK_HEADER_NAME, "").strip()
    if not expected or got != expected:
        raise HTTPException(status_code=401, detail="Header check failed")


@router.post("/webhook/jellyseerr")
async def jellyseerr_webhook(req: Request, x_jellyseerr_signature: str | None = Header(default=None, convert_underscores=False)):
    raw = await req.body()
    _verify_header(req)
    _verify_shared_secret(raw, x_jellyseerr_signature)
    payload = await req.json()
    return await handle_jellyseerr(payload)
