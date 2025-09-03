FROM python:3.11-slim
WORKDIR /app

# System deps (curl for healthcheck/debug)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ---- Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- App code
COPY app ./app

# ---- Conditional VERSION file (only for prod builds)
COPY VERSION* ./
# The * makes it optional - won't fail if VERSION doesn't exist

# ---- Version injection (for build-time override)
ARG REMEDIARR_VERSION=""
ENV REMEDIARR_VERSION=${REMEDIARR_VERSION}

# ---- Runtime
ENV PYTHONUNBUFFERED=1
CMD ["python", "-c", "import os, uvicorn; uvicorn.run('app.main:app', host=os.getenv('APP_HOST','0.0.0.0'), port=int(os.getenv('APP_PORT','8189')), proxy_headers=True)"]
