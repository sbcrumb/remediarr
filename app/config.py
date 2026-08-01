from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hard-coded to prevent loops and user customization
BOT_PREFIX = "[Remediarr]"


def env_alias(new_name: str, old_name: str, default: str = "") -> str:
    """Read new_name, falling back to old_name for backward compat (e.g. SEERR_* vs legacy JELLYSEERR_*)."""
    val = os.getenv(new_name)
    if val is not None:
        return val
    return os.getenv(old_name, default)


def _detect_version() -> str:
    """
    Version detection priority:
      1) VERSION file (for main/prod releases)
      2) Git SHA (for dev branch) 
      3) REMEDIARR_VERSION env (for build-time override)
      4) fallback 'dev'
    """
    # First check for VERSION file (production releases)
    vf = Path(__file__).resolve().parents[1] / "VERSION"
    if vf.exists():
        vtxt = vf.read_text(encoding="utf-8").strip()
        if vtxt:
            return vtxt

    # Try git SHA for development
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[1]
        ).decode().strip()
        return f"dev-{sha}"
    except Exception:
        pass

    # Environment override (build-time)
    env_v = os.getenv("REMEDIARR_VERSION")
    if env_v and env_v.strip():
        return env_v.strip()

    return "dev"


class Settings(BaseSettings):
    # ===== App / Server =====
    APP_NAME: str = "Remediarr"
    VERSION: str = _detect_version()
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8189

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

    # SEERR_* is the canonical name; JELLYSEERR_* is accepted for backward
    # compat since Jellyseerr/Seerr/Overseerr-based apps all speak the same API.
    SEERR_URL: str = Field(validation_alias=AliasChoices("SEERR_URL", "JELLYSEERR_URL"))
    SEERR_API_KEY: str = Field(validation_alias=AliasChoices("SEERR_API_KEY", "JELLYSEERR_API_KEY"))

    BAZARR_URL: Optional[str] = None
    BAZARR_API_KEY: Optional[str] = None
    BAZARR_HTTP_TIMEOUT: int = 60
    BAZARR_SUBTITLE_LANGUAGES: str = "en"
    BAZARR_FORCE_REDOWNLOAD: bool = False

    # ===== Behavior toggles =====
    SEERR_CLOSE_ISSUES: bool = Field(default=True, validation_alias=AliasChoices("SEERR_CLOSE_ISSUES", "JELLYSEERR_CLOSE_ISSUES"))
    SEERR_COMMENT_ON_ACTION: bool = Field(default=True, validation_alias=AliasChoices("SEERR_COMMENT_ON_ACTION", "JELLYSEERR_COMMENT_ON_ACTION"))
    # When true, the Seerr issue TYPE (audio/video/subtitle/other) drives the
    # remediation action and the comment is ignored. OFF by default.
    ISSUE_TYPE_AS_BUCKET: bool = False
    # When true, a TV remediation does NOT close the issue at search time; it
    # waits for Sonarr's "On Import" webhook (POST /webhook/sonarr) confirming the
    # replacement actually landed on disk before commenting success + closing.
    # OFF by default — the existing close-at-search-time behavior is unchanged.
    # Applies only to Sonarr file replacements (audio/video/subtitle); "other"
    # (search-only) and Bazarr-handled subtitle fixes still close at action time,
    # since neither produces a Sonarr import to wait for.
    CONFIRM_REPLACEMENT_IMPORT: bool = False
    # When true, before deleting a file Remediarr marks the grab that produced it as
    # failed in Radarr/Sonarr, which blocklists that release. Without this, a
    # re-search can grab the exact same broken release again and nothing changes.
    # OFF by default — the existing behavior is unchanged.
    BLOCKLIST_ON_REPLACE: bool = False

    # ===== Keyword buckets (comma-separated) =====
    TV_AUDIO_KEYWORDS: str = "no audio,no sound,missing audio,audio issue,wrong language,not in english"
    TV_VIDEO_KEYWORDS: str = "no video,video glitch,black screen,stutter,pixelation"
    TV_SUBTITLE_KEYWORDS: str = "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync"
    TV_OTHER_KEYWORDS: str = "buffering,playback error,corrupt file"
    TV_WRONG_KEYWORDS: str = "not the right show,wrong show,incorrect show,wrong episode,incorrect episode,not the right episode"

    MOVIE_AUDIO_KEYWORDS: str = "no audio,no sound,audio issue,wrong language,not in english"
    MOVIE_VIDEO_KEYWORDS: str = "no video,video missing,bad video,broken video,black screen"
    MOVIE_SUBTITLE_KEYWORDS: str = "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync"
    MOVIE_OTHER_KEYWORDS: str = "buffering,playback error,corrupt file"
    MOVIE_WRONG_KEYWORDS: str = "not the right movie,wrong movie,incorrect movie"

    # ===== Notifications =====
    APPRISE_URLS: Optional[str] = None
    GOTIFY_URL: Optional[str] = None
    GOTIFY_TOKEN: Optional[str] = None
    GOTIFY_PRIORITY: int = 5
    DISABLE_STARTUP_NOTIFICATION: bool = False

    # ===== Customizable messages =====
    MSG_MOVIE_SUCCESS: Optional[str] = None
    MSG_TV_SUCCESS: Optional[str] = None
    MSG_AUTOCLOSE_FAIL: Optional[str] = None
    MSG_COACH: Optional[str] = None
    # CONFIRM_REPLACEMENT_IMPORT messages: interim (posted at search time, no close)
    # and the confirmation (posted when Sonarr reports the import, then close).
    MSG_TV_SEARCHING: Optional[str] = None
    MSG_TV_IMPORTED: Optional[str] = None
    MSG_MOVIE_SEARCHING: Optional[str] = None
    MSG_MOVIE_IMPORTED: Optional[str] = None

    # ===== Cooldown =====
    REMEDIARR_ISSUE_COOLDOWN_SEC: int = 90

    # ===== Startup Health Check Settings =====
    STARTUP_HEALTH_CHECK_RETRIES: int = 3
    STARTUP_HEALTH_CHECK_DELAY: int = 10  # seconds between retries

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


cfg = Settings()
