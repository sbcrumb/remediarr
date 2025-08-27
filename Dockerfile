# syntax=docker/dockerfile:1.7

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Accept a build-time version (fallback to file copy)
ARG VERSION=0.0.0-dev
ENV APP_VERSION=${VERSION}

# Install deps with BuildKit cache
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# App code
COPY app.py /app/app.py

# Also copy the plaintext VERSION file (for local runs / safety)
COPY VERSION /app/VERSION

EXPOSE 8189
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8189"]
