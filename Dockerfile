FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install deps (cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app ./app

# Env & startup
ENV PYTHONUNBUFFERED=1
EXPOSE 8189
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8189", "--proxy-headers"]
