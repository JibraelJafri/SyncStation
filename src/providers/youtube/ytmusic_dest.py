import os
import time
import random
from typing import List, Optional, Dict, Any
from ytmusicapi import YTMusic

from src.core.models import Track, Playlist, CandidateTrack, ConnectionState
from src.core.config import AppConfig
from src.providers.base import MusicDestination
from src.domain.normalizer import clean_query_string, extract_title_and_featured
from src.core.logger import logger

class YouTubeMusicDestination(MusicDestination):
    def __init__(self):
        self.settings = AppConfig.get_settings()["youtube"]
        self.client: Optional[YTMusic] = None
        self.connection_state: ConnectionState = ConnectionState.NOT_CONFIGURED
        self._init_client()

    def _init_client(self):
        if not self.settings["configured"] or not self.settings["headers"]:
            self.connection_state = ConnectionState.NOT_CONFIGURED
            return
        try:
            self.connection_state = ConnectionState.CONNECTING
            proxy_val = (self.settings.get("proxy") or os.environ.get("YOUTUBE_PROXY") or "").strip()
            proxies_dict = {"http": proxy_val, "https": proxy_val} if proxy_val else None
            self.client = YTMusic(
                auth=self.settings["headers"],
                user=self.settings.get("client_id") or None,
                proxies=proxies_dict
            )
            self.connection_state = ConnectionState.CONNECTED
        except Exception as e:
            logger.warning(f"Could not initialize YouTube Music client: {e}")
            self.connection_state = ConnectionState.AUTH_FAILED
            self.client = None

    def is_available(self) -> bool:
        return self.client is not None and self.connection_state == ConnectionState.CONNECTED

    def test_connection(self) -> Dict[str, Any]:
        if not self.client:
            return {"connected": False, "state": self.connection_state.value, "message": "YouTube Music headers not configured"}
        try:
            # Test call by fetching user library playlists
            res = self.client.get_library_playlists(limit=5)
            return {
                "connected": True,
                "state": ConnectionState.CONNECTED.value,
                "message": f"YouTube Music session authenticated and active ({len(res)} library playlists detected)"
            }
        except Exception as e:
            err_str = str(e).lower()
            if "401" in err_str or "unauthorized" in err_str or "cookie" in err_str:
                self.connection_state = ConnectionState.EXPIRED
                return {"connected": False, "state": ConnectionState.EXPIRED.value, "message": "YouTube Music session cookie expired. Please renew your headers."}
            return {"connected": False, "state": ConnectionState.PROVIDER_UNAVAILABLE.value, "message": f"YouTube Music connection error: {e}"}

    def _execute_with_backoff(self, fn, *args, **kwargs):
        """Execute YouTube Music API request with backoff and jitter."""
        max_retries = 3
        delay = 0.5
        for attempt in range(max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if ("429" in err_str or "quota" in err_str or "temporarily" in err_str) and attempt < max_retries - 1:
                    sleep_time = delay + random.uniform(0.2, 0.6)
                    time.sleep(sleep_time)
                    delay *= 2
                elif attempt < max_retries - 1 and "50" in err_str:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise

    def search_candidates(self, track: Track, limit: int = 5) -> List[CandidateTrack]:
        if not self.client:
            return []

        clean_query = clean_query_string(track.artist, track.name)
        candidates = []

        try:
            # Phase 1: High-precision query filtered by songs
            results = self._execute_with_backoff(self.client.search, clean_query, filter="songs", limit=limit)
            
            # Phase 2: If no songs returned, search without filter (video/audio)
            if not results:
                results = self._execute_with_backoff(self.client.search, clean_query, limit=limit)

            # Phase 3: If still empty, search clean title
            if not results:
                clean_title, _ = extract_title_and_featured(track.name)
                if clean_title != clean_query:
                    results = self._execute_with_backoff(self.client.search, clean_title, filter="songs", limit=limit)

            for item in (results or []):
                vid = item.get("videoId")
                if not vid:
                    continue

                # Parse duration
                dur_str = item.get("duration", "0:00")
                duration = 0.0
                if dur_str and ":" in dur_str:
                    parts = dur_str.split(":")
                    if len(parts) == 2:
                        duration = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

                artists_list = [a["name"] for a in item.get("artists", [])] if "artists" in item else []
                artist_name = ", ".join(artists_list) if artists_list else ""
                album_name = item.get("album", {}).get("name", "") if isinstance(item.get("album"), dict) else ""
                thumb = item["thumbnails"][-1]["url"] if item.get("thumbnails") else None

                candidates.append(CandidateTrack(
                    video_id=vid,
                    title=item.get("title", ""),
                    artist=artist_name,
                    album=album_name,
                    duration_seconds=duration,
                    result_type=item.get("resultType", "song"),
                    thumbnail_url=thumb,
                    is_explicit=bool(item.get("isExplicit", False))
                ))

        except Exception as e:
            logger.error(f"Error searching YouTube Music for '{clean_query}': {e}")

        return candidates

    def get_playlist(self, playlist_id: str, limit: int = 5000) -> Optional[Playlist]:
        if not self.client:
            return None
        try:
            pl_data = self._execute_with_backoff(self.client.get_playlist, playlist_id, limit=limit)
            tracks = []
            for item in pl_data.get("tracks", []):
                vid = item.get("videoId")
                if vid:
                    artists = ", ".join([a["name"] for a in item.get("artists", [])]) if "artists" in item else ""
                    tracks.append(Track(
                        id=vid,
                        uri=f"ytm:track:{vid}",
                        name=item.get("title", ""),
                        artist=artists,
                        album=item.get("album", {}).get("name", "") if isinstance(item.get("album"), dict) else "",
                        duration_seconds=float(item.get("duration_seconds", 0))
                    ))
            return Playlist(
                id=playlist_id,
                uri=f"https://music.youtube.com/playlist?list={playlist_id}",
                name=pl_data.get("title", ""),
                description=pl_data.get("description", ""),
                track_count=len(tracks),
                tracks=tracks
            )
        except Exception as e:
            logger.error(f"Failed to fetch YouTube Music playlist {playlist_id}: {e}")
            return None

    def get_library_playlists(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.client:
            return []
        try:
            return self._execute_with_backoff(self.client.get_library_playlists, limit=limit)
        except Exception as e:
            logger.error(f"Failed to fetch YouTube Music library playlists: {e}")
            return []

    def create_playlist(self, name: str, description: str = "", privacy: str = "PRIVATE", video_ids: List[str] = None) -> str:
        if not self.client:
            raise RuntimeError("YouTube Music destination client not authenticated.")

        initial_batch = video_ids[:50] if video_ids else []
        pl_id = self._execute_with_backoff(
            self.client.create_playlist,
            title=name,
            description=description,
            privacy_status=privacy,
            video_ids=initial_batch
        )

        if video_ids and len(video_ids) > 50:
            remaining = video_ids[50:]
            self.add_tracks_to_playlist(pl_id, remaining)

        return pl_id

    def add_tracks_to_playlist(
        self,
        playlist_id: str,
        video_ids: List[str],
        progress_callback: Optional[Any] = None
    ) -> int:
        if not self.client or not video_ids:
            return 0

        chunk_size = 50
        added_count = 0
        total_to_add = len(video_ids)

        for i in range(0, total_to_add, chunk_size):
            chunk = video_ids[i:i + chunk_size]
            try:
                self._execute_with_backoff(self.client.add_playlist_items, playlist_id, chunk, duplicates=True)
                added_count += len(chunk)
                if progress_callback:
                    try:
                        progress_callback(added_count, total_to_add)
                    except Exception:
                        pass
                time.sleep(0.25)  # Polite pacing between chunk uploads
            except Exception as e:
                err_str = str(e).lower()
                logger.error(f"Error adding chunk of {len(chunk)} tracks to playlist {playlist_id}: {e}")
                if "404" in err_str or "not found" in err_str or "contents" in err_str:
                    logger.warning(f"Aborting upload: playlist {playlist_id} was deleted or not found on YouTube Music.")
                    break

        return added_count

    def remove_tracks_from_playlist(self, playlist_id: str, tracks_to_remove: Any) -> int:
        """Remove tracks from destination playlist using ytmusicapi remove_playlist_items."""
        if not self.client or not tracks_to_remove:
            return 0

        formatted = []
        for item in tracks_to_remove:
            if isinstance(item, str):
                formatted.append({"videoId": item})
            elif isinstance(item, dict):
                formatted.append(item)
            elif hasattr(item, "id"):
                formatted.append({"videoId": item.id})

        if not formatted:
            return 0

        try:
            self._execute_with_backoff(self.client.remove_playlist_items, playlist_id, formatted)
            return len(formatted)
        except Exception as e:
            logger.error(f"Error removing tracks from playlist {playlist_id}: {e}")
            return 0

    def rate_track(self, video_id: str, rating: str = "LIKE") -> bool:
        if not self.client:
            return False
        try:
            self._execute_with_backoff(self.client.rate_song, video_id, rating)
            return True
        except Exception as e:
            logger.error(f"Failed to rate track {video_id}: {e}")
            return False
