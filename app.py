import os
import re
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

# =========================
# Configuration (env)
# =========================
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8189"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Version (for /health)
APP_VERSION = os.getenv("APP_VERSION")  # injected at build
if not APP_VERSION:
    try:
        with open("/app/VERSION", "r", encoding="utf-8") as f:
            APP_VERSION = f.read().strip()
    except Exception:
        APP_VERSION = "0.0.0-dev"

# Webhook security
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "")
WEBHOOK_HEADER_NAME = os.getenv("WEBHOOK_HEADER_NAME", "X-Jellyseerr-Token")
WEBHOOK_HEADER_VALUE = os.getenv("WEBHOOK_HEADER_VALUE", "")

# Allow only these events to trigger actions
ALLOWED_EVENTS = [s.strip().lower() for s in os.getenv(
    "ALLOWED_EVENTS",
    "issue created,new comment"
).split(",") if s.strip()]

# Identify our own bot comments to avoid loops
JELLYSEERR_BOT_USERNAME = os.getenv("JELLYSEERR_BOT_USERNAME", "").strip()
OWN_COMMENT_PREFIX = os.getenv("OWN_COMMENT_PREFIX", "[Remediarr]")

# Jellyseerr API
JELLYSEERR_URL = (os.getenv("JELLYSEERR_URL", "") or "").rstrip("/")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "")
JELLYSEERR_CLOSE_ISSUES = os.getenv("JELLYSEERR_CLOSE_ISSUES", "false").lower() == "true"
JELLYSEERR_COMMENT_ON_ACTION = os.getenv("JELLYSEERR_COMMENT_ON_ACTION", "true").lower() == "true"
JELLYSEERR_COACH_REPORTERS = os.getenv("JELLYSEERR_COACH_REPORTERS", "true").lower() == "true"

# Sonarr / Radarr
SONARR_URL = (os.getenv("SONARR_URL", "http://sonarr:8989") or "").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")

RADARR_URL = (os.getenv("RADARR_URL", "http://radarr:7878") or "").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")

# TMDB
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SEARCH_ONLY_IF_DIGITAL_RELEASE = os.getenv("SEARCH_ONLY_IF_DIGITAL_RELEASE", "true").lower() == "true"

# Gotify
GOTIFY_URL = (os.getenv("GOTIFY_URL", "") or "").rstrip("/")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "")
GOTIFY_PRIORITY = int(os.getenv("GOTIFY_PRIORITY", "5"))

# Apprise (optional)
APPRISE_URLS = os.getenv("APPRISE_URLS", "").strip()  # comma/space separated

# Cooldown
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "25"))

# Keyword helpers
def _csv_env(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

# TV keywords
def TV_AUDIO():    return _csv_env("TV_AUDIO_KEYWORDS",    "no audio,no sound,missing audio,audio issue")
def TV_VIDEO():    return _csv_env("TV_VIDEO_KEYWORDS",    "no video,video glitch,black screen,stutter,pixelation")
def TV_SUBTITLE(): return _csv_env("TV_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
def TV_OTHER():    return _csv_env("TV_OTHER_KEYWORDS",    "buffering,playback error,corrupt file")

# Movie keywords
def MOV_AUDIO():    return _csv_env("MOVIE_AUDIO_KEYWORDS",    "no audio,no sound,audio issue")
def MOV_VIDEO():    return _csv_env("MOVIE_VIDEO_KEYWORDS",    "no video,video missing,bad video,broken video,black screen")
def MOV_SUBTITLE(): return _csv_env("MOVIE_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
def MOV_OTHER():    return _csv_env("MOVIE_OTHER_KEYWORDS",    "buffering,playback error,corrupt file")
def MOV_WRONG():    return _csv_env("MOVIE_WRONG_KEYWORDS",    "not the right movie,wrong movie,incorrect movie")

# Coaching message templates
def _env_msg(name: str, default: str) -> str:
    return os.getenv(name, default)

MSG_COACH_TV_AUDIO     = _env_msg("MSG_COACH_TV_AUDIO",     "[Remediarr] Tip: include one of these keywords to auto-fix TV audio (delete episode file + re-download): {keywords}.")
MSG_COACH_TV_VIDEO     = _env_msg("MSG_COACH_TV_VIDEO",     "[Remediarr] Tip: include one of these keywords to auto-fix TV video: {keywords}.")
MSG_COACH_TV_SUBTITLE  = _env_msg("MSG_COACH_TV_SUBTITLE",  "[Remediarr] Tip: include one of these keywords to auto-fix TV subtitles: {keywords}.")
MSG_COACH_TV_OTHER     = _env_msg("MSG_COACH_TV_OTHER",     "[Remediarr] Tip: include one of these keywords to trigger automation for TV other: {keywords}.")
MSG_COACH_MOV_AUDIO    = _env_msg("MSG_COACH_MOV_AUDIO",    "[Remediarr] Tip: include one of these keywords to auto-handle movie audio: {keywords}.")
MSG_COACH_MOV_VIDEO    = _env_msg("MSG_COACH_MOV_VIDEO",    "[Remediarr] Tip: include one of these keywords to auto-handle movie video: {keywords}.")
MSG_COACH_MOV_SUBTITLE = _env_msg("MSG_COACH_MOV_SUBTITLE", "[Remediarr] Tip: include one of these keywords to auto-handle movie subtitles: {keywords}.")
MSG_COACH_MOV_OTHER    = _env_msg("MSG_COACH_MOV_OTHER",    "[Remediarr] Tip: include one of these keywords to auto-handle movie other: {keywords}.")

MSG_TV_EP_REPLACED       = _env_msg("MSG_TV_EP_REPLACED",       "[Remediarr] {title} S{season:02d}E{episode:02d} – deleted file and re-download started.")
MSG_TV_EP_SEARCH_ONLY    = _env_msg("MSG_TV_EP_SEARCH_ONLY",    "[Remediarr] {title} S{season:02d}E{episode:02d} – re-download started.")
MSG_TV_OTHER_SEARCH_ONLY = _env_msg("MSG_TV_OTHER_SEARCH_ONLY", "[Remediarr] {title} S{season:02d}E{episode:02d} – search triggered (no delete).")

MSG_MOV_GENERIC_HANDLED   = _env_msg("MSG_MOV_GENERIC_HANDLED",   "[Remediarr] {title}: Blocklisted last grab, deleted {deleted} file(s), search started.")
MSG_MOV_WRONG_HANDLED     = _env_msg("MSG_MOV_WRONG_HANDLED",     "[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s), search started.")
MSG_MOV_WRONG_NO_RELEASE  = _env_msg("MSG_MOV_WRONG_NO_RELEASE",  "[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s). Not searching (not digitally released).")
MSG_AUTOCLOSE_FAIL        = _env_msg("MSG_AUTOCLOSE_FAIL",        "[Remediarr] Action completed but I couldn’t auto-close this issue. Please close it once you verify it’s fixed.")

# Logging
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] [%(levelname)s] %(message)s")
log = logging.getLogger("remediarr")

app = FastAPI(title="Remediarr")

# runtime cooldown memory
_last_action_at: Dict[str, datetime] = {}

# =========================
# Utils
# =========================
def _has_keyword(text: str, keywords: List[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

def _key(name: Any) -> str:
    return str(name or "")

async def _notify(title: str, message: str):
    # Gotify
    if GOTIFY_URL and GOTIFY_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(f"{GOTIFY_URL}/message?token={GOTIFY_TOKEN}",
                             json={"title": title, "message": message, "priority": GOTIFY_PRIORITY})
        except Exception as e:
            log.info("Gotify send failed: %s", e)
    # Apprise
    urls = [u for u in re.split(r"[\s,]+", APPRISE_URLS) if u]
    if urls:
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                for u in urls:
                    await c.post(u, data=message)
        except Exception as e:
            log.info("Apprise-like send failed: %s", e)

def _to_int_or_none(val) -> Optional[int]:
    try:
        if isinstance(val, bool):
            return None
        if isinstance(val, int):   return val
        if isinstance(val, float): return int(val)
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("{{") and s.endswith("}}"): return None
            m = re.search(r"\d+", s)
            return int(m.group()) if m else None
    except Exception:
        return None
    return None

def _extract_season_episode_from_text(text: str) -> Tuple[Optional[int], Optional[int]]:
    m = re.search(r"[Ss](\d{1,3})[Ee](\d{1,3})", text or "")
    if m: return int(m.group(1)), int(m.group(2))
    sm = re.search(r"Season\s+(\d{1,3})", text or "", re.IGNORECASE)
    em = re.search(r"Episode\s+(\d{1,3})", text or "", re.IGNORECASE)
    return (int(sm.group(1)) if sm else None, int(em.group(1)) if em else None)

def _extract_title_year_from_text(*texts: str) -> Tuple[Optional[str], Optional[int]]:
    blob = " ".join([t for t in texts if t])
    m = re.search(r"(.+?)\s*\((\d{4})\)", blob)
    if m:
        title = m.group(1).strip(" -"); year = _to_int_or_none(m.group(2))
        if title: return title, year
    m2 = re.search(r"([A-Za-z0-9'!&.,:-]{3,}(?:\s+[A-Za-z0-9'!&.,:-]{2,}){0,5})", blob)
    return (m2.group(1).strip(" -") if m2 else None, None)

def _key_looks_like(name: str, want: str) -> bool:
    n = name.lower()
    if want == "season":  return ("season" in n) and ("reason" not in n)
    if want == "episode": return "episode" in n
    return False

def _maybe_int_from_obj(v: Any) -> Optional[int]:
    if isinstance(v, dict):
        for _, v2 in v.items():
            iv = _to_int_or_none(v2)
            if iv is not None: return iv
    return _to_int_or_none(v)

def _walk_for_season_episode(o: Any) -> Tuple[Optional[int], Optional[int]]:
    s_found: Optional[int] = None; e_found: Optional[int] = None
    def _walk(node: Any):
        nonlocal s_found, e_found
        if node is None or (s_found is not None and e_found is not None): return
        if isinstance(node, dict):
            for k, v in node.items():
                if s_found is None and _key_looks_like(k, "season"):
                    sv = _maybe_int_from_obj(v);  s_found = sv if sv is not None else s_found
                if e_found is None and _key_looks_like(k, "episode"):
                    ev = _maybe_int_from_obj(v);  e_found = ev if ev is not None else e_found
                _walk(v)
        elif isinstance(node, list):
            for it in node: _walk(it)
    _walk(o);  return s_found, e_found

# =========================
# Signature verification
# =========================
async def verify_webhook(request: Request, jelly_signature: Optional[str], headers: Dict[str, str]) -> None:
    if WEBHOOK_SHARED_SECRET:
        body = await request.body()
        digest = hmac.new(WEBHOOK_SHARED_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not jelly_signature or not hmac.compare_digest(digest, jelly_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
    if WEBHOOK_HEADER_NAME and WEBHOOK_HEADER_VALUE:
        sent = headers.get(WEBHOOK_HEADER_NAME)
        if sent != WEBHOOK_HEADER_VALUE:
            raise HTTPException(status_code=401, detail="Invalid header token")

# =========================
# Sonarr helpers
# =========================
async def sonarr_get_series_by_tvdb(tvdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{SONARR_URL}/api/v3/series"
    params = {"apikey": SONARR_API_KEY, "tvdbId": tvdb_id}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params); r.raise_for_status()
        items = r.json();  return items[0] if isinstance(items, list) and items else None

async def sonarr_find_episode(series_id: int, season: int, episode: int) -> Optional[Dict[str, Any]]:
    url = f"{SONARR_URL}/api/v3/episode"
    params = {"apikey": SONARR_API_KEY, "seriesId": series_id, "seasonNumber": season}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params); r.raise_for_status()
        for ep in r.json():
            if ep.get("episodeNumber") == episode: return ep
        return None

async def sonarr_delete_episode_file(episode_file_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        await c.delete(f"{SONARR_URL}/api/v3/episodefile/{episode_file_id}", params={"apikey": SONARR_API_KEY})

async def sonarr_episode_search(episode_id: int) -> None:
    payload = {"name": "EpisodeSearch", "episodeIds": [episode_id]}
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{SONARR_URL}/api/v3/command", params={"apikey": SONARR_API_KEY}, json=payload)

# =========================
# Radarr helpers
# =========================
async def radarr_get_movie_by_tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/movie", params={"apikey": RADARR_API_KEY, "tmdbId": tmdb_id})
        r.raise_for_status(); items = r.json()
        return items[0] if isinstance(items, list) and items else None

async def radarr_list_movie_files(movie_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/moviefile", params={"apikey": RADARR_API_KEY, "movieId": movie_id})
        r.raise_for_status(); return r.json()

async def radarr_delete_movie_file(movie_file_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        await c.delete(f"{RADARR_URL}/api/v3/moviefile/{movie_file_id}", params={"apikey": RADARR_API_KEY})

async def radarr_get_movie_history(movie_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/history/movie", params={"apikey": RADARR_API_KEY, "movieId": movie_id})
        r.raise_for_status(); return r.json()

async def radarr_mark_history_failed(history_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{RADARR_URL}/api/v3/history/failed/{history_id}", params={"apikey": RADARR_API_KEY})

async def radarr_search_movie(movie_id: int) -> None:
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{RADARR_URL}/api/v3/command", params={"apikey": RADARR_API_KEY}, json=payload)

async def radarr_lookup_best_tmdb(title: str, year: Optional[int]) -> Optional[int]:
    if not title: return None
    async with httpx.AsyncClient(timeout=20) as c:
        candidates: List[Dict[str, Any]] = []
        for term in ([f"{title} ({year})"] if year else []) + [title]:
            r = await c.get(f"{RADARR_URL}/api/v3/movie/lookup", params={"apikey": RADARR_API_KEY, "term": term})
            r.raise_for_status()
            data = r.json()
            for it in (data if isinstance(data, list) else []):
                if isinstance(it, dict) and it.get("tmdbId"): candidates.append(it)
        if not candidates: return None
        if year:
            for it in candidates:
                it_year = _to_int_or_none(it.get("year")) or _to_int_or_none((it.get("releaseDate") or "")[:4])
                if it_year and it_year == year: return it.get("tmdbId")
        return candidates[0].get("tmdbId")

async def tmdb_is_digitally_released(tmdb_id: int) -> bool:
    if not TMDB_API_KEY: return False
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}", params={"api_key": TMDB_API_KEY})
            r.raise_for_status(); data = r.json()
            rd = data.get("release_date")
            if not rd: return False
            try: dt = datetime.fromisoformat(rd)
            except ValueError: dt = datetime.strptime(rd, "%Y-%m-%d")
            return dt <= datetime.now()
    except Exception as e:
        log.info("TMDB check failed: %s", e);  return False

# =========================
# Jellyseerr helpers
# =========================
def _jelly_headers() -> Dict[str, str]:
    return {
        "X-Api-Key": JELLYSEERR_API_KEY,
        "Authorization": f"Bearer {JELLYSEERR_API_KEY}",
        "Content-Type": "application/json",
    }

async def jellyseerr_comment_issue(issue_id: Any, message: str) -> bool:
    if not (JELLYSEERR_URL and JELLYSEERR_API_KEY and issue_id): return False
    paths = [f"/api/v1/issue/{issue_id}/comment", f"/api/v1/issues/{issue_id}/comments"]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            try:
                r = await c.post(f"{JELLYSEERR_URL}{p}", headers=_jelly_headers(), json={"message": message})
                if r.status_code in (200, 201, 204): return True
            except Exception: pass
    return False

async def jellyseerr_close_issue(issue_id: Any) -> bool:
    if not (JELLYSEERR_URL and JELLYSEERR_API_KEY and issue_id): return False
    attempts = [
        ("POST", f"/api/v1/issue/{issue_id}/resolve", None, {"status": "resolved"}),
        ("POST", f"/api/v1/issue/{issue_id}/status",  None, {"status": "resolved"}),
        ("POST", f"/api/v1/issues/{issue_id}/resolve", None, {"status": "resolved"}),
        ("POST", f"/api/v1/issues/{issue_id}/status",  None, {"status": "resolved"}),
        ("POST", f"/api/v1/issue/{issue_id}/status",  {"status": "resolved"}, None),
        ("POST", f"/api/v1/issue/{issue_id}/status",  {"isResolved": True}, None),
    ]
    async with httpx.AsyncClient(timeout=20) as c:
        for method, path, json_body, query in attempts:
            try:
                r = await c.request(method, f"{JELLYSEERR_URL}{path}",
                                    headers=_jelly_headers(), json=json_body, params=query)
                if r.status_code in (200, 201, 204): return True
                log.info("Close attempt %s %s -> %s %s", method, path, r.status_code, r.text[:180])
            except Exception as e:
                log.info("Close attempt error %s %s", path, e)
    return False

async def jellyseerr_fetch_issue(issue_id: Any) -> Tuple[Optional[int], Optional[int]]:
    if not (JELLYSEERR_URL and JELLYSEERR_API_KEY and issue_id): return None, None
    paths = [f"/api/v1/issue/{issue_id}", f"/api/v1/issues/{issue_id}"]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            r = await c.get(f"{JELLYSEERR_URL}{p}", headers=_jelly_headers())
            if r.status_code == 404: continue
            r.raise_for_status(); data = r.json()
            s, e = _walk_for_season_episode(data)
            if s is not None or e is not None: return s, e
    return None, None

# =========================
# TV / Movie helpers
# =========================
async def _tv_episode_from_payload(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], int, int]:
    issue = payload.get("issue") or {}; media = payload.get("media") or {}; comment = payload.get("comment") or {}
    tvdb_id = media.get("tvdbId") or media.get("tvdbid")
    if not tvdb_id: raise HTTPException(status_code=400, detail="Missing tvdbId")

    c_s = [media.get("seasonNumber"), media.get("season"),
           issue.get("affected_season"), issue.get("season")]
    c_e = [media.get("episodeNumber"), media.get("episode"),
           issue.get("affected_episode"), issue.get("episode")]
    season = next((v for v in (_to_int_or_none(x) for x in c_s) if v is not None), None)
    episode = next((v for v in (_to_int_or_none(x) for x in c_e) if v is not None), None)

    text = " ".join([
        str(payload.get("subject") or ""), str(issue.get("issue_type") or ""),
        str(issue.get("issue_status") or ""), str(payload.get("message") or ""),
        str(comment.get("comment_message") or "")
    ])

    if season is None or episode is None:
        s2, e2 = _extract_season_episode_from_text(text)
        season = season if season is not None else s2
        episode = episode if episode is not None else e2

    if (season is None or episode is None) and issue.get("issue_id"):
        s3, e3 = await jellyseerr_fetch_issue(issue.get("issue_id"))
        season = season if season is not None else s3
        episode = episode if episode is not None else e3

    series = await sonarr_get_series_by_tvdb(int(tvdb_id))
    if not series: raise HTTPException(status_code=404, detail="Series not found in Sonarr")
    if season is None or episode is None:
        raise HTTPException(status_code=400, detail="Missing season/episode after best-effort extraction")
    return series["id"], series, int(season), int(episode)

async def tv_delete_and_search_episode(series_id: int, season: int, episode: int) -> Tuple[bool, int]:
    ep = await sonarr_find_episode(series_id, season, episode)
    if not ep: raise HTTPException(status_code=404, detail="Episode not found in Sonarr")
    deleted = False
    ep_file_id = ep.get("episodeFileId")
    if ep_file_id and ep_file_id != 0:
        await sonarr_delete_episode_file(ep_file_id); deleted = True
    await sonarr_episode_search(ep["id"])
    return deleted, ep["id"]

async def _radarr_resolve_movie(payload: Dict[str, Any]) -> Tuple[int, str, Optional[int]]:
    media = payload.get("media") or {}; comment = payload.get("comment") or {}
    tmdb_id = media.get("tmdbId") or media.get("tmdbid")
    if not tmdb_id:
        subj = str(payload.get("subject") or ""); msg = str(payload.get("message") or "")
        cmsg = str(comment.get("comment_message") or "")
        title, year = _extract_title_year_from_text(subj, msg, cmsg)
        if not title: raise HTTPException(status_code=400, detail="Missing tmdbId and could not infer title/year")
        tmdb_id = await radarr_lookup_best_tmdb(title, year)
        if not tmdb_id: raise HTTPException(status_code=404, detail=f"Could not resolve movie '{title}'")
    movie = await radarr_get_movie_by_tmdb(int(tmdb_id))
    if not movie: raise HTTPException(status_code=404, detail="Movie not found in Radarr")
    return movie["id"], movie.get("title", f"tmdb-{tmdb_id}"), int(tmdb_id)

async def radarr_fail_last_delete_and_search(movie_id: int) -> int:
    history = await radarr_get_movie_history(movie_id)
    grabbed = next((h for h in history if (str(h.get("eventType") or "").lower() == "grabbed")), None)
    if grabbed: await radarr_mark_history_failed(int(grabbed["id"]))
    files = await radarr_list_movie_files(movie_id); deleted = 0
    for f in files:
        await radarr_delete_movie_file(int(f["id"])); deleted += 1
    await radarr_search_movie(movie_id)
    return deleted

# =========================
# API
# =========================
@app.get("/")
async def health():
    return {"ok": True, "service": "remediarr", "version": APP_VERSION}

@app.post("/webhook/jellyseerr")
async def jellyseerr_webhook(request: Request, x_jellyseerr_signature: Optional[str] = Header(default=None)):
    headers = {k: v for k, v in request.headers.items()}
    await verify_webhook(request, x_jellyseerr_signature, headers)

    payload = await request.json()
    event  = (payload.get("event") or "").lower()
    media  = payload.get("media") or {}
    issue  = payload.get("issue") or {}
    comment = payload.get("comment") or {}

    # Filter to allowed events only
    if not any(k in event for k in ALLOWED_EVENTS):
        log.info("Skipping event due to type: %s", event)
        return {"ok": True, "skipped": True, "reason": f"ignored event '{event}'"}

    media_type = (media.get("media_type") or media.get("mediaType") or "").lower()
    issue_type = (issue.get("issue_type") or "").lower()

    subject = str(payload.get("subject") or "")
    text = " ".join([
        subject, str(issue.get("issue_type") or ""), str(issue.get("issue_status") or ""),
        str(payload.get("message") or ""), str(comment.get("comment_message") or "")
    ]).strip()

    log.info("Received event=%s mediaType=%s issueType=%s desc=%r", event, media_type, issue_type, text)

    # Loop prevention: ignore our own comments / bot user
    commenter = (payload.get("comment", {}) or {}).get("commentedBy_username", "") or \
                (payload.get("comment", {}) or {}).get("commentedBy", "")
    if OWN_COMMENT_PREFIX and OWN_COMMENT_PREFIX.lower() in text.lower():
        log.info("Skipping: own comment prefix matched")
        return {"ok": True, "skipped": True, "reason": "own comment"}
    if JELLYSEERR_BOT_USERNAME and commenter and commenter.lower() == JELLYSEERR_BOT_USERNAME.lower():
        log.info("Skipping: bot user comment matched: %s", commenter)
        return {"ok": True, "skipped": True, "reason": "bot user comment"}

    # Cooldown guard (per issue id)
    issue_id = _key(issue.get("issue_id"))
    now = datetime.utcnow()
    last = _last_action_at.get(issue_id)

    # Helper: was this comment a "newly valid keyword" comment?
    def _now_matches_keywords() -> bool:
        if media_type == "tv":
            kmap = {"audio": TV_AUDIO(), "video": TV_VIDEO(), "subtitle": TV_SUBTITLE(), "other": TV_OTHER()}
        else:
            # for movie: either wrong-movie or per type
            kmap = {"audio": MOV_AUDIO(), "video": MOV_VIDEO(), "subtitle": MOV_SUBTITLE(), "other": MOV_OTHER()}
        if media_type == "movie" and _has_keyword(text, MOV_WRONG()):
            return True
        kws = kmap.get(issue_type, [])
        return _has_keyword(text, kws)

    newly_valid = _now_matches_keywords()
    if last and (now - last) < timedelta(seconds=COOLDOWN_SECONDS) and not newly_valid:
        log.info("Skipping: cooldown active for issue %s", issue_id or "<none>")
        return {"ok": True, "skipped": True, "reason": "cooldown active"}

    # ---------- Coaching (when enabled) ----------
    def coach_list(kw: List[str], label: str) -> str:
        return ", ".join([f"'{k}'" for k in kw[:5]]) or f"'<add {label} keywords>'"

    if JELLYSEERR_COACH_REPORTERS and issue_id:
        if media_type == "tv":
            if issue_type == "audio" and not _has_keyword(text, TV_AUDIO()):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_AUDIO.format(keywords=coach_list(TV_AUDIO(),"tv audio")))
                return {"ok": True, "skipped": True, "reason": "missing tv audio keywords"}
            if issue_type == "video" and not _has_keyword(text, TV_VIDEO()):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_VIDEO.format(keywords=coach_list(TV_VIDEO(),"tv video")))
                return {"ok": True, "skipped": True, "reason": "missing tv video keywords"}
            if issue_type == "subtitle" and not _has_keyword(text, TV_SUBTITLE()):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_SUBTITLE.format(keywords=coach_list(TV_SUBTITLE(),"tv subtitle")))
                return {"ok": True, "skipped": True, "reason": "missing tv subtitle keywords"}
            if issue_type == "other" and not _has_keyword(text, TV_OTHER()):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_OTHER.format(keywords=coach_list(TV_OTHER(),"tv other")))
                return {"ok": True, "skipped": True, "reason": "missing tv other keywords"}

        if media_type == "movie":
            if _has_keyword(text, MOV_WRONG()):
                pass  # handle below
            else:
                if issue_type == "audio" and not _has_keyword(text, MOV_AUDIO()):
                    await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_AUDIO.format(keywords=coach_list(MOV_AUDIO(),"movie audio")))
                    return {"ok": True, "skipped": True, "reason": "missing movie audio keywords"}
                if issue_type == "video" and not _has_keyword(text, MOV_VIDEO()):
                    await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_VIDEO.format(keywords=coach_list(MOV_VIDEO(),"movie video")))
                    return {"ok": True, "skipped": True, "reason": "missing movie video keywords"}
                if issue_type == "subtitle" and not _has_keyword(text, MOV_SUBTITLE()):
                    await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_SUBTITLE.format(keywords=coach_list(MOV_SUBTITLE(),"movie subtitle")))
                    return {"ok": True, "skipped": True, "reason": "missing movie subtitle keywords"}
                if issue_type == "other" and not _has_keyword(text, MOV_OTHER()):
                    await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_OTHER.format(keywords=coach_list(MOV_OTHER(),"movie other")))
                    return {"ok": True, "skipped": True, "reason": "missing movie other keywords"}

    # ---------- ACTIONS ----------
    # TV
    if media_type == "tv":
        try:
            series_id, series, season, episode = await _tv_episode_from_payload(payload)
        except HTTPException as e:
            log.info("TV action aborted: %s", e.detail)
            return {"ok": True, "skipped": True, "reason": e.detail}

        if issue_type == "other":
            ep = await sonarr_find_episode(series_id, season, episode)
            if ep:
                await sonarr_episode_search(ep["id"])
            msg = MSG_TV_OTHER_SEARCH_ONLY.format(title=series.get("title"), season=season, episode=episode)
            if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                await jellyseerr_comment_issue(issue_id, msg)
            await _notify("Remediarr – TV (other)", msg)
            _last_action_at[issue_id] = datetime.utcnow()
            return {"ok": True, "action": "tv_other_search_only"}

        deleted, ep_id = await tv_delete_and_search_episode(series_id, season, episode)
        msg = MSG_TV_EP_REPLACED.format(title=series.get("title"), season=season, episode=episode) if deleted \
              else MSG_TV_EP_SEARCH_ONLY.format(title=series.get("title"), season=season, episode=episode)
        if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
            await jellyseerr_comment_issue(issue_id, msg)

        closed = False
        if JELLYSEERR_CLOSE_ISSUES and issue_id:
            closed = await jellyseerr_close_issue(issue_id)
            if not closed:
                await jellyseerr_comment_issue(issue_id, MSG_AUTOCLOSE_FAIL)

        await _notify("Remediarr – TV", msg + (" (closed)" if closed else ""))
        _last_action_at[issue_id] = datetime.utcnow()
        return {"ok": True, "action": f"tv_{issue_type}", "deleted": deleted, "episodeId": ep_id, "closed": closed}

    # MOVIE
    if media_type == "movie":
        # wrong movie path has priority
        if _has_keyword(text, MOV_WRONG()):
            movie_id, title, tmdb_id = await _radarr_resolve_movie(payload)
            do_search = True
            if SEARCH_ONLY_IF_DIGITAL_RELEASE and tmdb_id:
                do_search = await tmdb_is_digitally_released(tmdb_id)

            if do_search:
                deleted = await radarr_fail_last_delete_and_search(movie_id)
                msg = MSG_MOV_WRONG_HANDLED.format(title=title, deleted=deleted)
            else:
                history = await radarr_get_movie_history(movie_id)
                grabbed = next((h for h in history if (str(h.get("eventType") or "").lower() == "grabbed")), None)
                if grabbed: await radarr_mark_history_failed(int(grabbed["id"]))
                files = await radarr_list_movie_files(movie_id)
                for f in files: await radarr_delete_movie_file(int(f["id"]))
                msg = MSG_MOV_WRONG_NO_RELEASE.format(title=title, deleted=len(files))

            if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                await jellyseerr_comment_issue(issue_id, msg)
            await _notify("Remediarr – Movie", msg)
            _last_action_at[issue_id] = datetime.utcnow()
            return {"ok": True, "action": "movie_wrong"}

        # per-type keywords
        kmap = {"audio": MOV_AUDIO(), "video": MOV_VIDEO(), "subtitle": MOV_SUBTITLE(), "other": MOV_OTHER()}
        if issue_type in kmap and _has_keyword(text, kmap[issue_type]):
            movie_id, title, _ = await _radarr_resolve_movie(payload)
            deleted = await radarr_fail_last_delete_and_search(movie_id)
            msg = MSG_MOV_GENERIC_HANDLED.format(title=title, deleted=deleted)
            if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                await jellyseerr_comment_issue(issue_id, msg)
            await _notify("Remediarr – Movie", msg)
            _last_action_at[issue_id] = datetime.utcnow()
            return {"ok": True, "action": f"movie_{issue_type}"}

    return {"ok": True, "skipped": True, "reason": "no rules matched"}