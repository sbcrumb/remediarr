# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

# Accept VERSION from CI (ghcr-main/dev workflows) and expose it as APP_VERSION
ARG VERSION=0.0.0-dev
ENV APP_VERSION=${VERSION}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Install Python deps first for better layer caching
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# 2) Copy source
COPY app ./app
COPY VERSION ./VERSION

# 3) Create an unprivileged user and take ownership
RUN adduser --disabled-password --gecos "" --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

# Optional built-in healthcheck (no wget/curl needed)
# If you also define a healthcheck in docker-compose, that will override this.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8189/').status==200 else 1)"

# Expose (documentation only)
EXPOSE 8189

# Run FastAPI (module path matches your new package structure)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8189"]
