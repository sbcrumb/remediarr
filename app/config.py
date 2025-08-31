from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Hard-coded to prevent loops and user customization
BOT_PREFIX = "[Remediarr]"


class Settings(BaseSettings):
    # ===== App / Server =====
    APP_NAME: str = "Remediarr"
    VERSION: str = os.getenv("REMEDIARR_VERSION", "0.3.0")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8189"))

    # ===== Webhook security =====
    WEBHOOK_SHARED_SECRET: Optional[str] = None   # HMAC "sha256=<hex>" in X-Jellyseerr-Signature
    WEBHOOK_HEADER_NAME: Optional[str] = None
    WEBHOOK_HEADER_VALUE: Optional[str] = None

    # ===== Integrations =====
    SONARR_URL: str
    SONARR_API_KEY: str
    SONARR_HTTP_TIMEOUT: int = 60

    RADARR_URL: str
    RADARR_API_KEY: str
    RADARR_HTTP_TIMEOUT: int = 60

    JELLYSEERR_URL: str
    JELLYSEERR_API_KEY: str

    # ===== Behavior toggles =====
    ENABLE_BLOCKLIST: bool = True   # (placeholder – not used here)
    CLOSE_JELLYSEERR_ISSUES: bool = True
    ACK_ON_COMMENT_CREATED: bool = True

    # Auto-close message (prefixed automatically)
    JELLYSEERR_CLOSE_MESSAGE: Optional[str] = None

    # ===== Verify windows =====
    RADARR_VERIFY_GRAB_SEC: int = 60
    RADARR_VERIFY_POLL_SEC: int = 5
    SONARR_VERIFY_GRAB_SEC: int = 60
    SONARR_VERIFY_POLL_SEC: int = 5

    # ===== Keyword buckets (comma-separated) =====
    TV_AUDIO_KEYWORDS: str = ""
    TV_VIDEO_KEYWORDS: str = ""
    TV_SUBTITLE_KEYWORDS: str = ""
    TV_OTHER_KEYWORDS: str = ""

    MOVIE_AUDIO_KEYWORDS: str = ""
    MOVIE_VIDEO_KEYWORDS: str = ""
    MOVIE_SUBTITLE_KEYWORDS: str = ""
    MOVIE_OTHER_KEYWORDS: str = ""
    MOVIE_WRONG_KEYWORDS: str = ""

    # ===== Notifications =====
    APPRISE_URLS: Optional[str] = None
    GOTIFY_URL: Optional[str] = None
    GOTIFY_TOKEN: Optional[str] = None
    GOTIFY_PRIORITY: int = 5

    # ===== Customizable messages =====
    MSG_MOVIE_REPLACED_AND_GRABBED: Optional[str] = None
    MSG_TV_REPLACED_AND_GRABBED: Optional[str] = None
    MSG_MOVIE_SEARCH_ONLY_GRABBED: Optional[str] = None
    MSG_TV_SEARCH_ONLY_GRABBED: Optional[str] = None

    MSG_TV_AUDIO_HANDLED: Optional[str] = None
    MSG_TV_VIDEO_HANDLED: Optional[str] = None
    MSG_TV_SUB_HANDLED: Optional[str] = None
    MSG_TV_OTHER_COACH: Optional[str] = None

    MSG_MOVIE_AUDIO_HANDLED: Optional[str] = None
    MSG_MOVIE_VIDEO_HANDLED: Optional[str] = None
    MSG_MOVIE_SUB_HANDLED: Optional[str] = None
    MSG_MOVIE_WRONG_HANDLED: Optional[str] = None
    MSG_MOVIE_OTHER_COACH: Optional[str] = None

    MSG_KEYWORD_COACH: Optional[str] = None
    MSG_AUTOCLOSE_FAIL: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


cfg = Settings()
