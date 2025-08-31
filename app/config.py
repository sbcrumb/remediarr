# app/config.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env if present (compose mounts it next to the app)
# We don't override process env, we just supplement it.
load_dotenv(dotenv_path=Path(".") / ".env", override=False)

# Hard-coded bot prefix used for tagging our own comments AND for
# self-loop protection. Users cannot change this in .env.
BOT_PREFIX = "[Remediarr]"

def _read_version() -> str:
    # Prefer ENV (set by Dockerfile: APP_VERSION), else VERSION file, else "dev"
    v = os.getenv("APP_VERSION")
    if v:
        return v
    try:
        here = Path(__file__).resolve().parents[1]
        return (here / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "dev"

class Settings(BaseSettings):
    # ----- Web server -----
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8189
    LOG_LEVEL: str = "INFO"

    # ----- Webhook security (optional) -----
    WEBHOOK_SHARED_SECRET: Optional[str] = None
    WEBHOOK_HEADER_NAME: str = "X-Jellyseerr-Token"
    WEBHOOK_HEADER_VALUE: Optional[str] = None

    # ----- Jellyseerr -----
    JELLYSEERR_URL: str = "http://jellyseerr:5055"
    JELLYSEERR_API_KEY: str = ""
    # Users can’t change the prefix; expose for templates only
    JELLYSEERR_BOT_COMMENT_PREFIX: str = BOT_PREFIX

    JELLYSEERR_CLOSE_ISSUES: bool = True
    JELLYSEERR_CLOSE_MESSAGE: str = (
        f"{BOT_PREFIX} Issue auto-closed after remediation. "
        "If anything’s still off, comment and I’ll take another pass."
    )

    JELLYSEERR_COACH_REPORTERS: bool = True
    JELLYSEERR_COMMENT_ON_ACTION: bool = True

    # Cooldown for self-loop protection (seconds)
    REMEDIARR_ISSUE_COOLDOWN_SEC: int = 90

    # ----- Sonarr / Radarr -----
    SONARR_URL: str = "http://sonarr:8989"
    SONARR_API_KEY: str = ""
    SONARR_HTTP_TIMEOUT: int = 60

    RADARR_URL: str = "http://radarr:7878"
    RADARR_API_KEY: str = ""
    RADARR_HTTP_TIMEOUT: int = 60

    # Tunables
    ACTION_SETTLE_SEC: int = 10
    RADARR_SEARCH_BACKOFF_SEC: int = 8
    RADARR_RSS_SYNC_BEFORE_SEARCH: bool = True
    RADARR_CONCURRENCY: int = 2
    SONARR_CONCURRENCY: int = 2

    # TMDB
    TMDB_API_KEY: str = ""
    PREFER_RADARR_RELEASE_DATE: bool = False
    SEARCH_ONLY_IF_DIGITAL_RELEASE: bool = True

    # Keywords
    TV_AUDIO_KEYWORDS: str = "no audio,no sound,missing audio,audio issue,wrong language,not in english"
    TV_VIDEO_KEYWORDS: str = "no video,video glitch,black screen,stutter,pixelation"
    TV_SUBTITLE_KEYWORDS: str = "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync"
    TV_OTHER_KEYWORDS: str = "buffering,playback error,corrupt file"

    MOVIE_AUDIO_KEYWORDS: str = "no audio,no sound,audio issue,wrong language,not in english"
    MOVIE_VIDEO_KEYWORDS: str = "no video,video missing,bad video,broken video,black screen"
    MOVIE_SUBTITLE_KEYWORDS: str = "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync"
    MOVIE_OTHER_KEYWORDS: str = "buffering,playback error,corrupt file"
    MOVIE_WRONG_KEYWORDS: str = "not the right movie,wrong movie,incorrect movie"

    # Behavior
    ENABLE_BLOCKLIST: bool = True

    # Notifications
    GOTIFY_URL: Optional[str] = None
    GOTIFY_TOKEN: Optional[str] = None
    GOTIFY_PRIORITY: int = 5
    APPRISE_URL: Optional[str] = None

    # Verification windows
    RADARR_VERIFY_GRAB_SEC: int = 60
    RADARR_VERIFY_POLL_SEC: int = 5
    SONARR_VERIFY_GRAB_SEC: int = 60
    SONARR_VERIFY_POLL_SEC: int = 5

    # Message templates (prefix is added by code; users can change the rest)
    MSG_TV_AUDIO_HANDLED: str = "Fixed TV audio issue automatically: {title} S{season:02d}E{episode:02d}"
    MSG_TV_VIDEO_HANDLED: str = "Fixed TV video issue automatically: {title} S{season:02d}E{episode:02d}"
    MSG_TV_SUB_HANDLED: str = "Fixed subtitle issue automatically: {title} S{season:02d}E{episode:02d}"
    MSG_TV_OTHER_COACH: str = "This type of TV issue can’t be auto-fixed. Please wait for admin review."

    MSG_MOVIE_AUDIO_HANDLED: str = "Fixed movie audio issue automatically: {title}"
    MSG_MOVIE_VIDEO_HANDLED: str = "Fixed movie video issue automatically: {title}"
    MSG_MOVIE_SUB_HANDLED: str = "Fixed movie subtitle issue automatically: {title}"
    MSG_MOVIE_WRONG_HANDLED: str = "Wrong movie reported – handled automatically: {title}"
    MSG_MOVIE_OTHER_COACH: str = "This type of movie issue can’t be auto-fixed. Please wait for admin review."

    MSG_KEYWORD_COACH: str = "Tip: include one of the auto-fix keywords next time so I can repair this automatically."
    MSG_AUTOCLOSE_FAIL: str = "Action completed but I couldn’t auto-close this issue. Please close it once you verify it’s fixed."

    MSG_MOVIE_REPLACED_AND_GRABBED: str = (
        "{title}: replaced file; new download grabbed. Closing this issue. "
        "If anything’s still off, comment and I’ll take another pass."
    )
    MSG_TV_REPLACED_AND_GRABBED: str = (
        "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. "
        "Closing this issue. If anything’s still off, comment and I’ll take another pass."
    )
    MSG_MOVIE_SEARCH_ONLY_GRABBED: str = (
        "{title}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass."
    )
    MSG_TV_SEARCH_ONLY_GRABBED: str = (
        "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue. "
        "If anything’s still off, comment and I’ll take another pass."
    )

    # Pydantic settings model cfg
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

class BuildInfo(BaseModel):
    app_version: str = _read_version()

# Export a module-level settings instance named `cfg`
cfg = Settings()
build = BuildInfo()

__all__ = ["cfg", "build", "BOT_PREFIX", "Settings", "BuildInfo"]
