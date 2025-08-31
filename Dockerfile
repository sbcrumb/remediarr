# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

# Build-time version (fed by GH Actions: build-args: APP_VERSION=...)
ARG APP_VERSION=0.0.0-dev
ENV APP_VERSION=${APP_VERSION}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Install deps first (better cache)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# 2) Copy source
COPY app ./app
COPY VERSION ./VERSION

 RUN useradd -m -u 1000 appuser
 USER appuser

# Expose (doc only)
EXPOSE 8189

# Run the FastAPI app from the package entry
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8189"]
