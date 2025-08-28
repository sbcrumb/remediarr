import os
import re
import hmac
import httpx
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Header, Request, HTTPException

# =========================
# Config / Environment
# =========================

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8189"))

WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "")
WEBHOOK_HEADER_NAME = os.getenv("WEBHOOK_HEADER_NAME", "X-Jellyseerr-Token")
WEBHOOK_HEADER_VALUE = os.getenv("WEBHOOK_HEADER_VALUE", "")

SONARR_URL = os.getenv("SONARR_URL", "http://sonarr:8989").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")

RADARR_URL = os.getenv("RADARR_URL", "http://radarr:7878").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SEARCH_ONLY_IF_DIGITAL_RELEASE = os.getenv("SEARCH_ONLY_IF_DIGITAL_RELEASE", "true").lower() == "true"

# Jellyseerr
JELLYSEERR_URL = os.getenv("JELLYSEERR_URL", "").rstrip("/")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "")
JELLYSEERR_CLOSE_ISSUES = os.getenv("JELLYSEERR_CLOSE_ISSUES", "false").lower() == "true"
JELLYSEERR_COMMENT_ON_ACTION = os.getenv("JELLYSEERR_COMMENT_ON_ACTION", "true").lower() == "true"
JELLYSEERR_COACH_REPORTERS = os.getenv("JELLYSEERR_COACH_REPORTERS", "true").lower() == "true"

# Loop-prevention
OWN_COMMENT_PREFIX = os.getenv("OWN_COMMENT_PREFIX", "[Remediarr]")
JELLYSEERR_BOT_USERNAME = os.getenv("JELLYSEERR_BOT_USERNAME", "")

# Notifications
GOTIFY_URL = os.getenv("GOTIFY_URL", "").rstrip("/")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "")
GOTIFY_PRIORITY = int(os.getenv("GOTIFY_PRIORITY", "5"))
APPRISE_URL = os.getenv("APPRISE_URL", "").strip()

# Keyword helpers
def _csv_env(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

# TV keywords
TV_AUDIO = _csv_env("TV_AUDIO_KEYWORDS", "no audio,no sound,missing audio,audio issue,wrong language,not english")
TV_VIDEO = _csv_env("TV_VIDEO_KEYWORDS", "no video,video glitch,black screen,stutter,pixelation")
TV_SUBTITLE = _csv_env("TV_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
TV_OTHER = _csv_env("TV_OTHER_KEYWORDS", "buffering,playback error,corrupt file")

# Movie keywords
MOV_AUDIO = _csv_env("MOVIE_AUDIO_KEYWORDS", "no audio,no sound,audio issue,wrong language,not english")
MOV_VIDEO = _csv_env("MOVIE_VIDEO_KEYWORDS", "no video,video missing,bad video,broken video,black screen")
MOV_SUBTITLE = _csv_env("MOVIE_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
MOV_OTHER = _csv_env("MOVIE_OTHER_KEYWORDS", "buffering,playback error,corrupt file")
MOV_WRONG = _csv_env("MOVIE_WRONG_KEYWORDS", "not the right movie,wrong movie,incorrect movie")

# Coaching messages (customizable)
def _msg_env(name: str, default: str) -> str:
    return os.getenv(name, default)

MSG_COACH_TV_AUDIO = _msg_env("MSG_COACH_TV_AUDIO",
    "[Remediarr] Tip: add one of: {keywords} to auto-fix TV audio (delete + re-download).")
MSG_COACH_TV_VIDEO = _msg_env("MSG_COACH_TV_VIDEO",
    "[Remediarr] Tip: add one of: {keywords} to auto-fix TV video.")
MSG_COACH_TV_SUBTITLE = _msg_env("MSG_COACH_TV_SUBTITLE",
    "[Remediarr] Tip: add one of: {keywords} to auto-fix TV subtitles.")
MSG_COACH_TV_OTHER = _msg_env("MSG_COACH_TV_OTHER",
    "[Remediarr] Tip: add one of: {keywords} to trigger automation for TV other.")

MSG_COACH_MOV_AUDIO = _msg_env("MSG_COACH_MOV_AUDIO",
    "[Remediarr] Tip: add one of: {keywords} to auto-handle movie audio.")
MSG_COACH_MOV_VIDEO = _msg_env("MSG_COACH_MOV_VIDEO",
    "[Remediarr] Tip: add one of: {keywords} to auto-handle movie video.")
MSG_COACH_MOV_SUBTITLE = _msg_env("MSG_COACH_MOV_SUBTITLE",
    "[Remediarr] Tip: add one of: {keywords} to auto-handle movie subtitles.")
MSG_COACH_MOV_OTHER = _msg_env("MSG_COACH_MOV_OTHER",
    "[Remediarr] Tip: add one of: {keywords} to auto-handle movie other.")

MSG_TV_REPLACED = _msg_env("MSG_TV_EP_REPLACED",
    "[Remediarr] {title} S{season:02d}E{episode:02d} – deleted file and re-download started.")
MSG_TV_OTHER_SEARCH = _msg_env("MSG_TV_OTHER_SEARCH_ONLY",
    "[Remediarr] {title} S{season:02d}E{episode:02d} – search triggered (no delete).")

MSG_MOV_GENERIC = _msg_env("MSG_MOV_GENERIC_HANDLED",
    "[Remediarr] {title}: blocklisted last grab, deleted {deleted} file(s), search started.")
MSG_MOV_WRONG = _msg_env("MSG_MOV_WRONG_HANDLED",
    "[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s), search started.")
MSG_MOV_WRONG_NO_RELEASE = _msg_env("MSG_MOV_WRONG_NO_RELEASE",
    "[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s). Not searching (not digitally released).")

MSG_AUTOCLOSE_FAIL = _msg_env("MSG_AUTOCLOSE_FAIL",
    "[Remediarr] Action completed but I couldn’t auto-close this issue. Please close it once verified.")

# Version
APP_VERSION = os.getenv("APP_VERSION", "")
if not APP_VERSION:
    try:
        with open("VERSION", "r") as f:
            APP_VERSION = f.read().strip()
    except FileNotFoundError:
        APP_VERSION = "0.0.0-dev"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="[%(asctime)s] [%(levelname)s] %(message)s")
log = logging.getLogger("remediarr")

# =========================
# App
# =========================

app = FastAPI(title="Remediarr")

# =========================
# Event Aliases / Allowed
# =========================

EVENT_ALIASES = {
    # created
    "issue_created": "issue_created",
    "issue created": "issue_created",
    "new issue reported": "issue_created",
    "new audio issue reported": "issue_created",
    "new video issue reported": "issue_created",
    "new subtitle issue reported": "issue_created",
    "new other issue reported": "issue_created",
    # commented
    "issue_commented": "issue_commented",
    "issue comment": "issue_commented",
    "new comment on audio issue": "issue_commented",
    "new comment on video issue": "issue_commented",
    "new comment on subtitle issue": "issue_commented",
    "new comment on other issue": "issue_commented",
    # reopened
    "issue_reopened": "issue_reopened",
    "issue reopened": "issue_reopened",
    # resolved (we generally ignore for actions)
    "issue_resolved": "issue_resolved",
    "issue resolved": "issue_resolved",
    "audio issue resolved": "issue_resolved",
    "video issue resolved": "issue_resolved",
    "subtitle issue resolved": "issue_resolved",
    "other issue resolved": "issue_resolved",
}
ALLOWED_EVENTS = {"issue_created", "issue_commented", "issue_reopened"}

# =========================
# Helpers / utils
# =========================

def _has_keyword(text: str, keywords: List[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

def _tips(keywords: List[str]) -> str:
    return ", ".join([f"'{k}'" for k in keywords[:5]]) or "'<add keyword>'"

async def _notify(title: str, message: str):
    if GOTIFY_URL and GOTIFY_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(f"{GOTIFY_URL}/message?token={GOTIFY_TOKEN}",
                             json={"title": title, "message": message, "priority": GOTIFY_PRIORITY})
        except Exception as e:
            log.warning("Gotify send failed: %s", e)
    if APPRISE_URL:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(APPRISE_URL, data={"title": title, "body": message})
        except Exception as e:
            log.warning("Apprise send failed: %s", e)

def _to_int_or_none(val) -> Optional[int]:
    try:
        if isinstance(val, bool):
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("{{") and s.endswith("}}"):
                return None
            m = re.search(r"\d+", s)
            return int(m.group()) if m else None
    except Exception:
        return None
    return None

def _extract_season_episode_from_text(text: str) -> Tuple[Optional[int], Optional[int]]:
    # S01E02
    m = re.search(r"[Ss](\d{1,3})[Ee](\d{1,3})", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    sm = re.search(r"Season\s+(\d{1,3})", text or "", re.IGNORECASE)
    em = re.search(r"Episode\s+(\d{1,3})", text or "", re.IGNORECASE)
    return (int(sm.group(1)) if sm else None, int(em.group(1)) if em else None)

def _extract_title_year_from_text(*parts: str) -> Tuple[Optional[str], Optional[int]]:
    blob = " ".join([p for p in parts if p])
    m = re.search(r"(.+?)\s*\((\d{4})\)", blob)
    if m:
        title = m.group(1).strip(" -")
        yr = _to_int_or_none(m.group(2))
        if title:
            return title, yr
    m2 = re.search(r"([A-Za-z0-9'!&.,:-]{3,}(?:\s+[A-Za-z0-9'!&.,:-]{2,}){0,6})", blob)
    return (m2.group(1).strip(" -") if m2 else None, None)

# =========================
# Signature / header check
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
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/series",
                        params={"apikey": SONARR_API_KEY, "tvdbId": tvdb_id})
        r.raise_for_status()
        items = r.json()
        return items[0] if isinstance(items, list) and items else None

async def sonarr_find_episode(series_id: int, season: int, episode: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SONARR_URL}/api/v3/episode",
                        params={"apikey": SONARR_API_KEY, "seriesId": series_id, "seasonNumber": season})
        r.raise_for_status()
        for ep in r.json():
            if ep.get("episodeNumber") == episode:
                return ep
        return None

async def sonarr_delete_episode_file(episode_file_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        await c.delete(f"{SONARR_URL}/api/v3/episodefile/{episode_file_id}",
                       params={"apikey": SONARR_API_KEY})

async def sonarr_episode_search(episode_id: int) -> None:
    payload = {"name": "EpisodeSearch", "episodeIds": [episode_id]}
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{SONARR_URL}/api/v3/command",
                     params={"apikey": SONARR_API_KEY}, json=payload)

# =========================
# Radarr helpers
# =========================

async def radarr_get_movie_by_tmdb(tmdb_id: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/movie",
                        params={"apikey": RADARR_API_KEY, "tmdbId": tmdb_id})
        r.raise_for_status()
        items = r.json()
        return items[0] if isinstance(items, list) and items else None

async def radarr_list_movie_files(movie_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/moviefile",
                        params={"apikey": RADARR_API_KEY, "movieId": movie_id})
        r.raise_for_status()
        return r.json()

async def radarr_delete_movie_file(movie_file_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        await c.delete(f"{RADARR_URL}/api/v3/moviefile/{movie_file_id}",
                       params={"apikey": RADARR_API_KEY})

async def radarr_get_movie_history(movie_id: int) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{RADARR_URL}/api/v3/history/movie",
                        params={"apikey": RADARR_API_KEY, "movieId": movie_id})
        r.raise_for_status()
        return r.json()

async def radarr_mark_history_failed(history_id: int) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{RADARR_URL}/api/v3/history/failed/{history_id}",
                     params={"apikey": RADARR_API_KEY})

async def radarr_search_movie(movie_id: int) -> None:
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(f"{RADARR_URL}/api/v3/command",
                     params={"apikey": RADARR_API_KEY}, json=payload)

async def radarr_lookup_best_tmdb(title: str, year: Optional[int]) -> Optional[int]:
    if not title:
        return None
    async with httpx.AsyncClient(timeout=20) as c:
        candidates: List[Dict[str, Any]] = []
        terms = [title]
        if year:
            terms = [f"{title} ({year})"] + terms
        for term in terms:
            r = await c.get(f"{RADARR_URL}/api/v3/movie/lookup",
                            params={"apikey": RADARR_API_KEY, "term": term})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                for it in data:
                    if isinstance(it, dict) and it.get("tmdbId"):
                        candidates.append(it)
        if not candidates:
            return None
        if year:
            for it in candidates:
                it_year = _to_int_or_none(it.get("year")) or _to_int_or_none((it.get("releaseDate") or "")[:4])
                if it_year and it_year == year:
                    return it.get("tmdbId")
        return candidates[0].get("tmdbId")

# =========================
# TMDB check (digital release)
# =========================

async def tmdb_is_digitally_released(tmdb_id: int) -> bool:
    if not TMDB_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                            params={"api_key": TMDB_API_KEY})
            r.raise_for_status()
            data = r.json()
            rd = data.get("release_date")
            if not rd:
                return False
            try:
                dt = datetime.fromisoformat(rd)
            except ValueError:
                dt = datetime.strptime(rd, "%Y-%m-%d")
            return dt <= datetime.now()
    except Exception as e:
        log.info("TMDB check failed: %s", e)
        return False

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
    if not (JELLYSEERR_URL and JELLYSEERR_API_KEY and issue_id):
        return False
    paths = [f"/api/v1/issue/{issue_id}/comment", f"/api/v1/issues/{issue_id}/comments"]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            try:
                r = await c.post(f"{JELLYSEERR_URL}{p}", headers=_jelly_headers(), json={"message": message})
                if r.status_code in (200, 201, 204):
                    return True
            except Exception:
                pass
    return False

async def jellyseerr_fetch_issue(issue_id: Any) -> Tuple[Optional[int], Optional[int]]:
    if not (JELLYSEERR_URL and JELLYSEERR_API_KEY and issue_id):
        return None, None
    paths = [f"/api/v1/issue/{issue_id}", f"/api/v1/issues/{issue_id}"]
    async with httpx.AsyncClient(timeout=20) as c:
        for p in paths:
            r = await c.get(f"{JELLYSEERR_URL}{p}", headers=_jelly_headers())
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            # dig for season/episode anywhere
            s = None
            e = None
            def _walk(o):
                nonlocal s, e
                if isinstance(o, dict):
                    for k, v in o.items():
                        lk = k.lower()
                        if s is None and ("season" in lk and "reason" not in lk):
                            s = _to_int_or_none(v) if not isinstance(v, dict) else _to_int_or_none(list(v.values())[0])
                        if e is None and "episode" in lk:
                            e = _to_int_or_none(v) if not isinstance(v, dict) else _to_int_or_none(list(v.values())[0])
                        _walk(v)
                elif isinstance(o, list):
                    for it in o:
                        _walk(it)
            _walk(data)
            if s is not None or e is not None:
                return s, e
    return None, None

async def jellyseerr_close_issue(issue_id: Any) -> bool:
    if not (JELLYSEERR_URL and JELLYSEERR_API_KEY and issue_id):
        return False
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
                if r.status_code in (200, 201, 204):
                    return True
                log.info("Close attempt %s %s -> %s %s", method, path, r.status_code, r.text[:160])
            except Exception as e:
                log.info("Close attempt error %s %s", path, e)
    return False

# =========================
# Health
# =========================

@app.get("/")
async def health():
    return {"ok": True, "service": "remediarr", "version": APP_VERSION}

# =========================
# Cooldown (per-issue)
# =========================

_COOLDOWN = {}
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "25"))

def cooldown_active(issue_id: Any) -> bool:
    if not issue_id:
        return False
    ts = _COOLDOWN.get(str(issue_id))
    if not ts:
        return False
    return datetime.utcnow() < ts

def touch_cooldown(issue_id: Any):
    if not issue_id:
        return
    _COOLDOWN[str(issue_id)] = datetime.utcnow() + timedelta(seconds=COOLDOWN_SECONDS)

# =========================
# Webhook
# =========================

@app.post("/webhook/jellyseerr")
async def jellyseerr_webhook(request: Request, x_jellyseerr_signature: Optional[str] = Header(default=None)):
    headers = {k: v for k, v in request.headers.items()}
    await verify_webhook(request, x_jellyseerr_signature, headers)

    payload = await request.json()
    raw_event = (payload.get("event") or "").strip().lower()
    event = EVENT_ALIASES.get(raw_event, raw_event)

    # Only process core events
    if event not in ALLOWED_EVENTS:
        log.info("Skipping event due to type: %s (raw=%r)", event, raw_event)
        return {"ok": True, "skipped": True, "reason": f"ignored event '{raw_event}'"}

    media = payload.get("media") or {}
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}

    media_type = (media.get("media_type") or media.get("mediaType") or "").lower()
    issue_type = (issue.get("issue_type") or "").lower()

    subject = str(payload.get("subject") or "")
    text = " ".join([
        subject, str(issue.get("issue_type") or ""), str(issue.get("issue_status") or ""),
        str(payload.get("message") or ""), str(comment.get("comment_message") or "")
    ]).strip()

    log.info("Received event=%s mediaType=%s issueType=%s desc=%r", event, media_type, issue_type, text)

    issue_id = issue.get("issue_id") or issue.get("id")

    # Prevent loops: ignore our own comments / a bot account if configured
    commenter = (comment or {}).get("commentedBy_username") or (comment or {}).get("commentedBy") or ""
    if OWN_COMMENT_PREFIX and OWN_COMMENT_PREFIX.lower() in text.lower():
        log.info("Skipping: own comment prefix matched")
        return {"ok": True, "skipped": True, "reason": "own comment"}
    if JELLYSEERR_BOT_USERNAME and commenter and commenter.lower() == JELLYSEERR_BOT_USERNAME.lower():
        log.info("Skipping: bot user comment matched: %s", commenter)
        return {"ok": True, "skipped": True, "reason": "bot user comment"}

    # Cooldown—EXCEPT if this is a comment that now includes a valid keyword
    bypass_cooldown = False
    kw_any = MOV_AUDIO + MOV_VIDEO + MOV_SUBTITLE + MOV_OTHER + TV_AUDIO + TV_VIDEO + TV_SUBTITLE + TV_OTHER + MOV_WRONG
    if event == "issue_commented" and _has_keyword(text, kw_any):
        bypass_cooldown = True

    if not bypass_cooldown and cooldown_active(issue_id):
        log.info("Skipping: cooldown active for issue %s", issue_id)
        return {"ok": True, "skipped": True, "reason": "cooldown active"}

    # ----- Coaching before action (if enabled) -----
    if JELLYSEERR_COACH_REPORTERS and issue_id:
        if media_type == "tv":
            if issue_type == "audio" and not _has_keyword(text, TV_AUDIO):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_AUDIO.format(keywords=_tips(TV_AUDIO)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing tv audio keywords"}
            if issue_type == "video" and not _has_keyword(text, TV_VIDEO):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_VIDEO.format(keywords=_tips(TV_VIDEO)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing tv video keywords"}
            if issue_type == "subtitle" and not _has_keyword(text, TV_SUBTITLE):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_SUBTITLE.format(keywords=_tips(TV_SUBTITLE)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing tv subtitle keywords"}
            if issue_type == "other" and not _has_keyword(text, TV_OTHER):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_TV_OTHER.format(keywords=_tips(TV_OTHER)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing tv other keywords"}

        elif media_type == "movie" and not _has_keyword(text, MOV_WRONG):
            if issue_type == "audio" and not _has_keyword(text, MOV_AUDIO):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_AUDIO.format(keywords=_tips(MOV_AUDIO)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing movie audio keywords"}
            if issue_type == "video" and not _has_keyword(text, MOV_VIDEO):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_VIDEO.format(keywords=_tips(MOV_VIDEO)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing movie video keywords"}
            if issue_type == "subtitle" and not _has_keyword(text, MOV_SUBTITLE):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_SUBTITLE.format(keywords=_tips(MOV_SUBTITLE)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing movie subtitle keywords"}
            if issue_type == "other" and not _has_keyword(text, MOV_OTHER):
                await jellyseerr_comment_issue(issue_id, MSG_COACH_MOV_OTHER.format(keywords=_tips(MOV_OTHER)))
                touch_cooldown(issue_id)
                return {"ok": True, "skipped": True, "reason": "missing movie other keywords"}

    # ----- Actions -----

    # TV: delete ep file + episode search (except "other" which is search-only)
    if media_type == "tv":
        try:
            tvdb_id = media.get("tvdbId") or media.get("tvdbid")
            if not tvdb_id:
                raise HTTPException(status_code=400, detail="Missing tvdbId")

            # Gather season/episode
            season = _to_int_or_none(media.get("seasonNumber")) or _to_int_or_none(issue.get("affected_season")) \
                     or _to_int_or_none(issue.get("season"))
            episode = _to_int_or_none(media.get("episodeNumber")) or _to_int_or_none(issue.get("affected_episode")) \
                      or _to_int_or_none(issue.get("episode"))

            if season is None or episode is None:
                s2, e2 = _extract_season_episode_from_text(text)
                season = season if season is not None else s2
                episode = episode if episode is not None else e2

            if (season is None or episode is None) and issue_id:
                s3, e3 = await jellyseerr_fetch_issue(issue_id)
                season = season if season is not None else s3
                episode = episode if episode is not None else e3

            series = await sonarr_get_series_by_tvdb(int(tvdb_id))
            if not series:
                raise HTTPException(status_code=404, detail="Series not found in Sonarr")
            if season is None or episode is None:
                raise HTTPException(status_code=400, detail="Missing season/episode after extraction")

            ep = await sonarr_find_episode(series["id"], int(season), int(episode))
            if not ep:
                raise HTTPException(status_code=404, detail="Episode not found in Sonarr")

            if issue_type == "other":
                await sonarr_episode_search(ep["id"])
                msg = MSG_TV_OTHER_SEARCH.format(title=series.get("title", "Unknown"),
                                                 season=int(season), episode=int(episode))
                if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                    await jellyseerr_comment_issue(issue_id, msg)
                touch_cooldown(issue_id)
                await _notify("Remediarr – TV (other)", msg)
                return {"ok": True, "action": "tv_other_search_only"}

            deleted = False
            ep_file_id = ep.get("episodeFileId")
            if ep_file_id and ep_file_id != 0:
                await sonarr_delete_episode_file(int(ep_file_id))
                deleted = True
            await sonarr_episode_search(ep["id"])

            msg = MSG_TV_REPLACED.format(title=series.get("title", "Unknown"),
                                         season=int(season), episode=int(episode))
            if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                await jellyseerr_comment_issue(issue_id, msg)

            closed = False
            if JELLYSEERR_CLOSE_ISSUES and issue_id:
                closed = await jellyseerr_close_issue(issue_id)
                if not closed:
                    await jellyseerr_comment_issue(issue_id, MSG_AUTOCLOSE_FAIL)

            touch_cooldown(issue_id)
            await _notify("Remediarr – TV", msg + (" (closed)" if closed else ""))
            return {"ok": True, "action": f"tv_{issue_type}", "deleted": deleted, "closed": closed}
        except HTTPException as e:
            log.info("TV action aborted: %s", e.detail)
            return {"ok": True, "skipped": True, "reason": e.detail}

    # Movies
    if media_type == "movie":
        # Resolve movie object
        async def _resolve_movie(payload: Dict[str, Any]) -> Tuple[int, str, Optional[int]]:
            media = payload.get("media") or {}
            comment = payload.get("comment") or {}
            tmdb_id = media.get("tmdbId") or media.get("tmdbid")
            if not tmdb_id:
                subj = str(payload.get("subject") or "")
                msg = str(payload.get("message") or "")
                cmsg = str(comment.get("comment_message") or "")
                title, year = _extract_title_year_from_text(subj, msg, cmsg)
                if not title:
                    raise HTTPException(status_code=400, detail="Missing tmdbId and could not infer title/year")
                tmdb_id = await radarr_lookup_best_tmdb(title, year)
                if not tmdb_id:
                    raise HTTPException(status_code=404, detail=f"Could not resolve movie '{title}'")
            movie = await radarr_get_movie_by_tmdb(int(tmdb_id))
            if not movie:
                raise HTTPException(status_code=404, detail="Movie not found in Radarr")
            return movie["id"], movie.get("title", f"tmdb-{tmdb_id}"), int(tmdb_id)

        # wrong-movie gets priority
        if _has_keyword(text, MOV_WRONG):
            movie_id, title, tmdb_id = await _resolve_movie(payload)
            do_search = True
            if SEARCH_ONLY_IF_DIGITAL_RELEASE and tmdb_id:
                do_search = await tmdb_is_digitally_released(tmdb_id)

            # Mark last grabbed as failed, delete file(s)
            history = await radarr_get_movie_history(movie_id)
            grabbed = next((h for h in history if (str(h.get("eventType") or "").lower() == "grabbed")), None)
            if grabbed:
                await radarr_mark_history_failed(int(grabbed["id"]))

            files = await radarr_list_movie_files(movie_id)
            deleted = 0
            for f in files:
                await radarr_delete_movie_file(int(f["id"]))
                deleted += 1

            if do_search:
                await radarr_search_movie(movie_id)
                msg = MSG_MOV_WRONG.format(title=title, deleted=deleted)
            else:
                msg = MSG_MOV_WRONG_NO_RELEASE.format(title=title, deleted=deleted)

            if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                await jellyseerr_comment_issue(issue_id, msg)

            touch_cooldown(issue_id)
            await _notify("Remediarr – Movie", msg)
            return {"ok": True, "action": "movie_wrong", "title": title}

        # Otherwise respect selected type’s keywords
        kmap = {
            "audio": MOV_AUDIO,
            "video": MOV_VIDEO,
            "subtitle": MOV_SUBTITLE,
            "other": MOV_OTHER,
        }
        if issue_type in kmap and _has_keyword(text, kmap[issue_type]):
            movie_id, title, _tmdb = await _resolve_movie(payload)

            # Mark last grabbed as failed, delete file(s)
            history = await radarr_get_movie_history(movie_id)
            grabbed = next((h for h in history if (str(h.get("eventType") or "").lower() == "grabbed")), None)
            if grabbed:
                await radarr_mark_history_failed(int(grabbed["id"]))

            files = await radarr_list_movie_files(movie_id)
            deleted = 0
            for f in files:
                await radarr_delete_movie_file(int(f["id"]))
                deleted += 1

            await radarr_search_movie(movie_id)
            msg = MSG_MOV_GENERIC.format(title=title, deleted=deleted)

            if JELLYSEERR_COMMENT_ON_ACTION and issue_id:
                await jellyseerr_comment_issue(issue_id, msg)

            touch_cooldown(issue_id)
            await _notify("Remediarr – Movie", msg)
            return {"ok": True, "action": f"movie_{issue_type}", "title": title}

    return {"ok": True, "skipped": True, "reason": "no rules matched"}
