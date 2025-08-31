from fastapi import APIRouter
from app.webhooks.handlers import handle_jellyseerr

router = APIRouter()

@router.post("/webhook/jellyseerr")
async def jellyseerr_webhook(payload: dict):
    return await handle_jellyseerr(payload)
