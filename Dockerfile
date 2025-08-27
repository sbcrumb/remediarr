FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (tiny; add more if you need ffmpeg or others later)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy only the app code (keeps image small)
COPY app.py /app/app.py

EXPOSE 8189
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8189"]
