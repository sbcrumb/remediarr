import os

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8189"))

VERSION = os.getenv("APP_VERSION") or (
    open("VERSION").read().strip() if os.path.exists("VERSION") else "0.0.0-dev"
)

# Common service configs
SONARR_URL = os.getenv("SONARR_URL", "http://sonarr:8989")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")

RADARR_URL = os.getenv("RADARR_URL", "http://radarr:7878")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")

JELLYSEERR_URL = os.getenv("JELLYSEERR_URL", "http://jellyseerr:5055")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "")

GOTIFY_URL = os.getenv("GOTIFY_URL", "")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "")