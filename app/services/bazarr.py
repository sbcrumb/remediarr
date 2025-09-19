import os
import logging
from typing import Any, Dict, List, Optional
import httpx
from app.config import cfg

log = logging.getLogger("remediarr")

def _get_preferred_language() -> str:
    """Get the first preferred subtitle language from config."""
    languages = cfg.BAZARR_SUBTITLE_LANGUAGES.split(",")
    return languages[0].strip() if languages else "en"

def _should_force_redownload() -> bool:
    """Check if we should force re-download of existing subtitles."""
    return cfg.BAZARR_FORCE_REDOWNLOAD

BASE = cfg.BAZARR_URL.rstrip("/") if cfg.BAZARR_URL else ""
API = f"{BASE}/api"
KEY = cfg.BAZARR_API_KEY or ""
HEADERS = {"X-API-KEY": KEY} if KEY else {}
TIMEOUT = cfg.BAZARR_HTTP_TIMEOUT

_client: Optional[httpx.AsyncClient] = None
def _client_lazy() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client

async def get_series_by_tvdb(tvdb: int) -> Optional[Dict[str, Any]]:
    """Get series by TVDB ID from Bazarr."""
    r = await _client_lazy().get(f"{API}/series", headers=HEADERS)
    r.raise_for_status()
    series_list = r.json() or []
    
    for series in series_list:
        # Bazarr stores TVDB ID in tvdbId field
        if series.get("tvdbId") == tvdb:
            return series
    return None

async def get_movie_by_tmdb(tmdb: int) -> Optional[Dict[str, Any]]:
    """Get movie by TMDB ID from Bazarr."""
    r = await _client_lazy().get(f"{API}/movies", headers=HEADERS)
    r.raise_for_status()
    movies_list = r.json() or []
    
    for movie in movies_list:
        # Bazarr stores TMDB ID in tmdbId field
        if movie.get("tmdbId") == str(tmdb):  # Bazarr may store as string
            return movie
    return None

async def search_episode_subtitles(series_id: int, episode_id: int, language: Optional[str] = None) -> bool:
    """Search for subtitles for a specific episode."""
    if language is None:
        language = _get_preferred_language()
        
    try:
        # Try to trigger subtitle search for episode
        body = {
            "episodePath": "",  # Will be populated by Bazarr
            "sceneName": "",
            "language": language,
            "hi": False,
            "forced": False
        }
        
        r = await _client_lazy().post(
            f"{API}/episodes/{episode_id}/subtitles", 
            headers=HEADERS, 
            json=body
        )
        
        if r.status_code in (200, 201, 202):
            log.info("Bazarr: triggered subtitle search for episode %s", episode_id)
            return True
        else:
            log.warning("Bazarr: subtitle search failed for episode %s: %s", episode_id, r.status_code)
            return False
            
    except Exception as e:
        log.error("Bazarr: error searching subtitles for episode %s: %s", episode_id, e)
        return False

async def search_movie_subtitles(movie_id: int, language: Optional[str] = None) -> bool:
    """Search for subtitles for a specific movie."""
    if language is None:
        language = _get_preferred_language()
        
    try:
        # Try to trigger subtitle search for movie
        body = {
            "moviePath": "",  # Will be populated by Bazarr
            "sceneName": "",
            "language": language,
            "hi": False,
            "forced": False
        }
        
        r = await _client_lazy().post(
            f"{API}/movies/{movie_id}/subtitles", 
            headers=HEADERS, 
            json=body
        )
        
        if r.status_code in (200, 201, 202):
            log.info("Bazarr: triggered subtitle search for movie %s", movie_id)
            return True
        else:
            log.warning("Bazarr: subtitle search failed for movie %s: %s", movie_id, r.status_code)
            return False
            
    except Exception as e:
        log.error("Bazarr: error searching subtitles for movie %s: %s", movie_id, e)
        return False

async def delete_episode_subtitles(episode_id: int, language: Optional[str] = None) -> int:
    """Delete existing subtitles for an episode to force re-download."""
    if language is None:
        language = _get_preferred_language()
        
    # Only delete if force redownload is enabled
    if not _should_force_redownload():
        log.info("Bazarr: skipping subtitle deletion (force redownload disabled)")
        return 0
        
    try:
        # Get episode details to find subtitle files
        r = await _client_lazy().get(f"{API}/episodes/{episode_id}", headers=HEADERS)
        if r.status_code != 200:
            log.warning("Bazarr: could not get episode %s details", episode_id)
            return 0
            
        episode_data = r.json()
        subtitles = episode_data.get("subtitles", [])
        
        deleted = 0
        for subtitle in subtitles:
            if subtitle.get("code2") == language:
                subtitle_id = subtitle.get("id")
                if subtitle_id:
                    del_r = await _client_lazy().delete(
                        f"{API}/episodes/{episode_id}/subtitles/{subtitle_id}", 
                        headers=HEADERS
                    )
                    if del_r.status_code in (200, 204):
                        deleted += 1
                        log.info("Bazarr: deleted subtitle %s for episode %s", subtitle_id, episode_id)
        
        return deleted
        
    except Exception as e:
        log.error("Bazarr: error deleting subtitles for episode %s: %s", episode_id, e)
        return 0

async def delete_movie_subtitles(movie_id: int, language: Optional[str] = None) -> int:
    """Delete existing subtitles for a movie to force re-download."""
    if language is None:
        language = _get_preferred_language()
        
    # Only delete if force redownload is enabled
    if not _should_force_redownload():
        log.info("Bazarr: skipping subtitle deletion (force redownload disabled)")
        return 0
        
    try:
        # Get movie details to find subtitle files
        r = await _client_lazy().get(f"{API}/movies/{movie_id}", headers=HEADERS)
        if r.status_code != 200:
            log.warning("Bazarr: could not get movie %s details", movie_id)
            return 0
            
        movie_data = r.json()
        subtitles = movie_data.get("subtitles", [])
        
        deleted = 0
        for subtitle in subtitles:
            if subtitle.get("code2") == language:
                subtitle_id = subtitle.get("id")
                if subtitle_id:
                    del_r = await _client_lazy().delete(
                        f"{API}/movies/{movie_id}/subtitles/{subtitle_id}", 
                        headers=HEADERS
                    )
                    if del_r.status_code in (200, 204):
                        deleted += 1
                        log.info("Bazarr: deleted subtitle %s for movie %s", subtitle_id, movie_id)
        
        return deleted
        
    except Exception as e:
        log.error("Bazarr: error deleting subtitles for movie %s: %s", movie_id, e)
        return 0

async def trigger_wanted_search(media_type: str = "both") -> bool:
    """Trigger search for all wanted subtitles."""
    try:
        if media_type in ("series", "both"):
            r = await _client_lazy().post(f"{API}/system/tasks", headers=HEADERS, json={"taskid": "search_wanted_subtitles_series"})
            if r.status_code not in (200, 201, 202):
                log.warning("Bazarr: failed to trigger wanted series subtitles search")
        
        if media_type in ("movies", "both"):
            r = await _client_lazy().post(f"{API}/system/tasks", headers=HEADERS, json={"taskid": "search_wanted_subtitles_movies"})
            if r.status_code not in (200, 201, 202):
                log.warning("Bazarr: failed to trigger wanted movies subtitles search")
        
        log.info("Bazarr: triggered wanted subtitles search for %s", media_type)
        return True
        
    except Exception as e:
        log.error("Bazarr: error triggering wanted search: %s", e)
        return False

async def get_episode_by_sonarr_id(sonarr_episode_id: int) -> Optional[Dict[str, Any]]:
    """Get Bazarr episode by Sonarr episode ID."""
    try:
        # This may need adjustment based on actual Bazarr API structure
        r = await _client_lazy().get(f"{API}/episodes", headers=HEADERS)
        r.raise_for_status()
        episodes = r.json() or []
        
        for episode in episodes:
            if episode.get("sonarrEpisodeId") == sonarr_episode_id:
                return episode
        return None
        
    except Exception as e:
        log.error("Bazarr: error getting episode by Sonarr ID %s: %s", sonarr_episode_id, e)
        return None

async def get_movie_by_radarr_id(radarr_movie_id: int) -> Optional[Dict[str, Any]]:
    """Get Bazarr movie by Radarr movie ID."""
    try:
        # This may need adjustment based on actual Bazarr API structure
        r = await _client_lazy().get(f"{API}/movies", headers=HEADERS)
        r.raise_for_status()
        movies = r.json() or []
        
        for movie in movies:
            if movie.get("radarrId") == radarr_movie_id:
                return movie
        return None
        
    except Exception as e:
        log.error("Bazarr: error getting movie by Radarr ID %s: %s", radarr_movie_id, e)
        return None