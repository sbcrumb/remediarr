from __future__ import annotations

import logging
import os


def _level() -> int:
    raw = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, raw, logging.INFO)


logging.basicConfig(
    level=_level(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("remediarr")