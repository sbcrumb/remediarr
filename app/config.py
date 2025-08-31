import os
import logging

# ------------ logging ------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] [%(levelname)s] %(message)s")
log = logging.getLogger("remediarr")

# ------------ server ------------
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8189"))

# ------------ fixed bot comment prefix ------------
# This value is NOT configurable via .env and is also used for self-comment detection.
COMMENT_PREFIX = "[Remediarr]"

# ------------ webhook auth (optional) ------------
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "")
WEBHOOK_HEADER_NAME = os.getenv("WEBHOOK_HEADER_NAME", "X-Jellyseerr-Token")
WEBHOOK_HEADER_VALUE = os.getenv("WEBHOOK_HEADER_VALUE", "")

# ------------ jellyseerr ------------
JELLYSEERR_URL = (os.getenv("JELLYSEERR_URL", "") or "").rstrip("/")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "") or ""
JELLYSEERR_CLOSE_ISSUES = os.getenv("JELLYSEERR_CLOSE_ISSUES", "true").lower() == "true"
# Optional final close message; we will auto-prefix with COMMENT_PREFIX at runtime.
JELLYSEERR_CLOSE_MESSAGE = (os.getenv("JELLYSEERR_CLOSE_MESSAGE", "") or "").strip()

# ------------ radarr ------------
RADARR_URL = (os.getenv("RADARR_URL", "http://radarr:7878") or "").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "") or ""
RADARR_HTTP_TIMEOUT = float(os.getenv("RADARR_HTTP_TIMEOUT", "60"))

# Verification window after we trigger searches
RADARR_VERIFY_GRAB_SEC = int(os.getenv("RADARR_VERIFY_GRAB_SEC", "60"))
RADARR_VERIFY_POLL_SEC = int(os.getenv("RADARR_VERIFY_POLL_SEC", "5"))

# ------------ sonarr ------------
SONARR_URL = (os.getenv("SONARR_URL", "http://sonarr:8989") or "").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "") or ""
SONARR_HTTP_TIMEOUT = float(os.getenv("SONARR_HTTP_TIMEOUT", "60"))

SONARR_VERIFY_GRAB_SEC = int(os.getenv("SONARR_VERIFY_GRAB_SEC", "60"))
SONARR_VERIFY_POLL_SEC = int(os.getenv("SONARR_VERIFY_POLL_SEC", "5"))

# ------------ behavior ------------
ENABLE_BLOCKLIST = os.getenv("ENABLE_BLOCKLIST", "true").lower() == "true"

# ------------ notifications (optional) ------------
GOTIFY_URL = (os.getenv("GOTIFY_URL", "") or "").rstrip("/")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "") or ""
GOTIFY_PRIORITY = int(os.getenv("GOTIFY_PRIORITY", "5"))

APPRISE_URL = (os.getenv("APPRISE_URL", "") or "").rstrip("/")
APPRISE_TARGETS = [u.strip() for u in (os.getenv("APPRISE_TARGETS", "") or "").split(",") if u.strip()]

# ------------ keywords (fully configurable) ------------
def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default) or ""
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

TV_AUDIO = _csv("TV_AUDIO_KEYWORDS", "no audio,no sound,missing audio,audio issue,wrong language,not in english")
TV_VIDEO = _csv("TV_VIDEO_KEYWORDS", "no video,video glitch,black screen,stutter,pixelation")
TV_SUBTITLE = _csv("TV_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
TV_OTHER = _csv("TV_OTHER_KEYWORDS", "buffering,playback error,corrupt file")

MOV_AUDIO = _csv("MOVIE_AUDIO_KEYWORDS", "no audio,no sound,audio issue,wrong language,not in english")
MOV_VIDEO = _csv("MOVIE_VIDEO_KEYWORDS", "no video,video missing,bad video,broken video,black screen")
MOV_SUBTITLE = _csv("MOVIE_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
MOV_OTHER = _csv("MOVIE_OTHER_KEYWORDS", "buffering,playback error,corrupt file")
MOV_WRONG = _csv("MOVIE_WRONG_KEYWORDS", "not the right movie,wrong movie,incorrect movie")

# ------------ message helpers ------------
def _msg(name: str, default: str) -> str:
    # Users may include or omit the prefix; we’ll ensure it at send time.
    return (os.getenv(name, default) or "").strip()

# Coaching message templates (kept configurable, disabled by default in handlers)
MSG_TV_AUDIO_HANDLED = _msg("MSG_TV_AUDIO_HANDLED", "Fixed TV audio issue automatically: {title} S{season:02d}E{episode:02d}")
MSG_TV_VIDEO_HANDLED = _msg("MSG_TV_VIDEO_HANDLED", "Fixed TV video issue automatically: {title} S{season:02d}E{episode:02d}")
MSG_TV_SUB_HANDLED   = _msg("MSG_TV_SUB_HANDLED",   "Fixed subtitle issue automatically: {title} S{season:02d}E{episode:02d}")
MSG_TV_OTHER_COACH   = _msg("MSG_TV_OTHER_COACH",   "This type of TV issue can’t be auto-fixed. Please wait for admin review.")

MSG_MOVIE_AUDIO_HANDLED = _msg("MSG_MOVIE_AUDIO_HANDLED", "Fixed movie audio issue automatically: {title}")
MSG_MOVIE_VIDEO_HANDLED = _msg("MSG_MOVIE_VIDEO_HANDLED", "Fixed movie video issue automatically: {title}")
MSG_MOVIE_SUB_HANDLED   = _msg("MSG_MOVIE_SUB_HANDLED",   "Fixed movie subtitle issue automatically: {title}")
MSG_MOVIE_WRONG_HANDLED = _msg("MSG_MOVIE_WRONG_HANDLED", "Wrong movie reported – handled automatically: {title}")
MSG_MOVIE_OTHER_COACH   = _msg("MSG_MOVIE_OTHER_COACH",   "This type of movie issue can’t be auto-fixed. Please wait for admin review.")

MSG_KEYWORD_COACH = _msg("MSG_KEYWORD_COACH", "Tip: include one of the auto-fix keywords next time so I can repair this automatically.")
MSG_AUTOCLOSE_FAIL = _msg("MSG_AUTOCLOSE_FAIL", "Action completed but I couldn’t auto-close this issue. Please close it once you verify it’s fixed.")

# One-shot success comments (used after we verify a GRAB)
MSG_MOVIE_REPLACED_AND_GRABBED = _msg(
    "MSG_MOVIE_REPLACED_AND_GRABBED",
    "{title}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_TV_REPLACED_AND_GRABBED = _msg(
    "MSG_TV_REPLACED_AND_GRABBED",
    "{title} S{season:02d}E{episode:02d}: replaced file; new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_MOVIE_SEARCH_ONLY_GRABBED = _msg(
    "MSG_MOVIE_SEARCH_ONLY_GRABBED",
    "{title}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
MSG_TV_SEARCH_ONLY_GRABBED = _msg(
    "MSG_TV_SEARCH_ONLY_GRABBED",
    "{title} S{season:02d}E{episode:02d}: new download grabbed. Closing this issue. If anything’s still off, comment and I’ll take another pass.",
)
