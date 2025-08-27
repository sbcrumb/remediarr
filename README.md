# Remediarr

A lightweight webhook service that listens to **Jellyseerr** issue webhooks and
automatically remediates common problems using your indexer/automation stack.

- TV issues: delete the bad episode and trigger a re-download.
- Movie issues: mark the last bad grab as failed, delete the bad file(s), and trigger a new search.
- “Wrong movie” smart path: optionally only re-search if the title has a digital release.
- Coaching mode: if the report lacks **keywords**, Remediarr comments tips instead of acting.
- Optional Gotify notifications.

> All user-facing comment texts and keyword lists are customizable via `.env`.

## Quick Start

1. Copy `.env.example` → `.env` and fill in your URLs/API keys.
2. Build & run:
   ```bash
   docker compose -f docker-compose.example.yml up -d --build
