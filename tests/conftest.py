import os

# app.config builds a Settings() at import time with required integration fields
# (SONARR_URL, etc.). Provide dummies so importing the modules under test does
# not fail during collection.
for _k, _v in {
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "x",
    "RADARR_URL": "http://radarr:7878",
    "RADARR_API_KEY": "x",
    "JELLYSEERR_URL": "http://seerr:5055",
    "JELLYSEERR_API_KEY": "x",
}.items():
    os.environ.setdefault(_k, _v)
