# Vulture false positives: these are only referenced via FastAPI route/event
# decorators or pytest autouse fixtures, so vulture can't see they're used.
# This file is NOT for silencing genuinely dead code — real orphaned
# functions/modules should be deleted, not whitelisted.
root  # app/main.py — FastAPI route
health  # app/main.py — FastAPI route
health_detailed  # app/main.py — FastAPI route
on_startup  # app/main.py — FastAPI startup event
jellyseerr_webhook  # app/webhooks/router.py — FastAPI route
sonarr_webhook  # app/webhooks/router.py — FastAPI route
radarr_webhook  # app/webhooks/router.py — FastAPI route
_clean_pending  # tests/test_router_auth.py — pytest autouse fixture
_stub_series  # tests/test_all_seasons_season0.py — pytest autouse fixture
