from fastapi import FastAPI
from app.webhooks.router import router as webhook_router
from app.config import VERSION

app = FastAPI(title="Remediarr")

# Register routes
app.include_router(webhook_router)

@app.get("/")
async def root():
    return {"ok": True, "service": "remediarr", "version": VERSION}
