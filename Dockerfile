FROM python:3.11-slim

WORKDIR /app

# System deps (curl for healthcheck/debug)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ---- Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- App code
COPY app ./app
COPY VERSION ./VERSION

# ---- Version injection (dev builds only)
ARG REMEDIARR_VERSION=""
ENV REMEDIARR_VERSION=${REMEDIARR_VERSION}

# ---- Runtime
ENV PYTHONUNBUFFERED=1

# Use a small launcher so APP_HOST/APP_PORT from .env are respected
CMD ["python", "-c", "import os, uvicorn; uvicorn.run('app.main:app', host=os.getenv('APP_HOST','0.0.0.0'), port=int(os.getenv('APP_PORT','8189')), proxy_headers=True)"]
