import time
import random
from typing import List, Optional, Dict, Any
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth, SpotifyOauthError
from spotipy.exceptions import SpotifyException

from src.core.models import Playlist, Track, ConnectionState
from src.core.config import AppConfig, SETTINGS_INI_PATH
from src.providers.base import MusicSource
from src.core.logger import logger

class SpotifyApiSource(MusicSource):
    def __init__(self):
        self.settings = AppConfig.get_settings()["spotify"]
        self.client: Optional[spotipy.Spotify] = None
        self.connection_state: ConnectionState = ConnectionState.NOT_CONFIGURED
        self._init_client()

    def _init_client(self):
        if not self.settings["configured"] or not self.settings["client_id"]:
            self.connection_state = ConnectionState.NOT_CONFIGURED
            return

        c_id = self.settings["client_id"]
        c_secret = self.settings.get("client_secret", "")
        use_oauth = self.settings.get("use_oauth", False)

        try:
            self.connection_state = ConnectionState.CONNECTING
            if use_oauth:
                auth = SpotifyOAuth(
                    client_id=c_id,
                    client_secret=c_secret,
                    redirect_uri="http://127.0.0.1:8765/api/auth/spotify/callback",
                    scope="user-library-read playlist-read-private playlist-read-collaborative",
                    open_browser=False
                )
                self.client = spotipy.Spotify(auth_manager=auth)
            else:
                auth = SpotifyClientCredentials(client_id=c_id, client_secret=c_secret)
                self.client = spotipy.Spotify(client_credentials_manager=auth)
            
            # Verify connectivity
            self.connection_state = ConnectionState.CONNECTED
        except SpotifyOauthError as e:
            logger.warning(f"Spotify authentication expired or failed: {e}")
            self.connection_state = ConnectionState.EXPIRED
            self.client = None
        except Exception as e:
            logger.warning(f"Could not initialize Spotify API client: {e}")
            self.connection_state = ConnectionState.AUTH_FAILED
            self.client = None

    _cached_result: Optional[Dict[str, Any]] = None
    _cached_time: float = 0.0

    def is_available(self) -> bool:
        return self.client is not None and self.connection_state == ConnectionState.CONNECTED

    def test_connection(self, force_refresh: bool = False) -> Dict[str, Any]:
        if not self.client:
            return {"connected": False, "state": self.connection_state.value, "message": "Spotify client not initialized"}
        
        # Return cached result if checked within last 30 seconds
        if not force_refresh and SpotifyApiSource._cached_result and (time.time() - SpotifyApiSource._cached_time < 30.0):
            return SpotifyApiSource._cached_result

        result: Dict[str, Any] = {}
        try:
            # Test connectivity with current user or public user profile
            if self.settings.get("use_oauth", False):
                me = self.client.current_user()
                user_name = me.get("display_name") or me.get("id") or "Connected User"
                result = {"connected": True, "state": ConnectionState.CONNECTED.value, "message": f"Spotify account authenticated ({user_name})"}
            else:
                self.client.user("spotify")
                result = {"connected": True, "state": ConnectionState.CONNECTED.value, "message": "Spotify Client Credentials API connected and healthy"}
        except SpotifyException as e:
            err_text = str(e).lower()
            if e.http_status == 403 and "premium" in err_text:
                self.connection_state = ConnectionState.AUTH_FAILED
                result = {
                    "connected": False,
                    "state": "RESTRICTED",
                    "message": "Spotify API restricted: Active Premium subscription required on the developer app owner. (Use '+ Add Spotify URL' to sync live without developer restrictions)."
                }
            elif e.http_status in [401, 403]:
                self.connection_state = ConnectionState.EXPIRED
                result = {"connected": False, "state": ConnectionState.EXPIRED.value, "message": f"Spotify token expired or unauthorized ({e.http_status}). Re-authentication required."}
            else:
                result = {"connected": False, "state": ConnectionState.PROVIDER_UNAVAILABLE.value, "message": f"Spotify API error: {e}"}
        except Exception as e:
            result = {"connected": False, "state": ConnectionState.AUTH_FAILED.value, "message": str(e)}

        SpotifyApiSource._cached_result = result
        SpotifyApiSource._cached_time = time.time()
        return result


    def _execute_with_retry(self, fn, *args, **kwargs):
        """Execute Spotify API calls with exponential backoff on HTTP 429/500."""
        max_retries = 3
        delay = 1.0
        for attempt in range(max_retries):
            try:
                return fn(*args, **kwargs)
            except SpotifyException as e:
                if e.http_status == 429:
                    retry_after = int(e.headers.get("Retry-After", delay))
                    time.sleep(retry_after + random.uniform(0.1, 0.5))
                elif e.http_status >= 500 and attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise

    def get_playlists(self) -> List[Playlist]:
        if not self.client:
            return []
        try:
            playlists = []
            results = self._execute_with_retry(self.client.current_user_playlists, limit=50)
            while results:
                for item in results.get("items", []):
                    if not item:
                        continue
                    playlists.append(Playlist(
                        id=item["id"],
                        uri=item["uri"],
                        name=item.get("name", "Untitled Playlist"),
                        description=item.get("description", ""),
                        track_count=item.get("tracks", {}).get("total", 0),
                        image_url=item["images"][0]["url"] if item.get("images") else None,
                        source_type="api"
                    ))
                if results.get("next"):
                    results = self._execute_with_retry(self.client.next, results)
                else:
                    break
            return playlists
        except Exception as e:
            logger.error(f"Failed to fetch Spotify user playlists: {e}")
            return []

    def get_playlist_tracks(self, playlist_id_or_url: str) -> Playlist:
        if not self.client:
            raise RuntimeError("Spotify API client not authenticated.")

        p_id = playlist_id_or_url.split("playlist/")[-1].split("?")[0] if "playlist/" in playlist_id_or_url else playlist_id_or_url
        
        pl_data = self._execute_with_retry(self.client.playlist, p_id)
        name = pl_data.get("name", "Spotify Playlist")
        desc = pl_data.get("description", "")
        img = pl_data["images"][0]["url"] if pl_data.get("images") else None

        tracks = []
        total = pl_data["tracks"]["total"]
        offset = 0

        while offset < total:
            items_page = self._execute_with_retry(self.client.playlist_items, p_id, offset=offset, limit=100)
            for it in items_page.get("items", []):
                t = it.get("track")
                if not t or not t.get("name"):
                    continue
                artists = ", ".join([a["name"] for a in t.get("artists", [])])
                tracks.append(Track(
                    id=t.get("id", ""),
                    uri=t.get("uri", f"spotify:track:{t.get('id', '')}"),
                    name=t["name"],
                    artist=artists,
                    album=t.get("album", {}).get("name", ""),
                    duration_seconds=t.get("duration_ms", 0) / 1000.0,
                    isrc=t.get("external_ids", {}).get("isrc"),
                    is_explicit=t.get("explicit", False),
                    image_url=t["album"]["images"][0]["url"] if t.get("album", {}).get("images") else None
                ))
            offset += 100

        return Playlist(
            id=p_id,
            uri=f"spotify:playlist:{p_id}",
            name=name,
            description=desc or "",
            track_count=len(tracks),
            image_url=img,
            tracks=tracks,
            source_type="api"
        )

    def get_liked_tracks(self) -> List[Track]:
        if not self.client:
            return []
        try:
            tracks = []
            results = self._execute_with_retry(self.client.current_user_saved_tracks, limit=50)
            while results:
                for it in results.get("items", []):
                    t = it.get("track")
                    if t and t.get("name"):
                        artists = ", ".join([a["name"] for a in t.get("artists", [])])
                        tracks.append(Track(
                            id=t.get("id", ""),
                            uri=t.get("uri", f"spotify:track:{t.get('id', '')}"),
                            name=t["name"],
                            artist=artists,
                            album=t.get("album", {}).get("name", ""),
                            duration_seconds=t.get("duration_ms", 0) / 1000.0,
                            isrc=t.get("external_ids", {}).get("isrc")
                        ))
                if results.get("next"):
                    results = self._execute_with_retry(self.client.next, results)
                else:
                    break
            return tracks
        except Exception as e:
            logger.error(f"Failed to fetch Spotify liked tracks: {e}")
            return []
