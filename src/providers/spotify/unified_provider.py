from pathlib import Path
from typing import List, Dict, Any, Optional

from src.core.models import Playlist, Track, SpotifySnapshotMetadata
from src.providers.base import MusicSource
from src.providers.spotify.export_source import SpotifyExportSource
from src.providers.spotify.api_source import SpotifyApiSource
from src.providers.spotify.web_source import SpotifyWebSource
from src.core.config import AppConfig
from src.core.logger import logger

from src.core.database import DatabaseManager

class UnifiedSpotifyProvider(MusicSource):
    """
    Unified Ingestion Facade: Normalizes data ingestion across Spotify API,
    Data Porter / Export Snapshots, and Web Scraper into identical internal models,
    with user-controlled mode switching.
    """
    def __init__(self, default_source_type: Optional[str] = None, file_path: Optional[str] = None):
        stored_mode = DatabaseManager.get_app_preference("active_source_mode", "api")
        self.source_type = default_source_type or stored_mode or "api"
        self.file_path = Path(file_path) if file_path else None
        self._export_source = SpotifyExportSource(file_path=self.file_path)
        self._api_source = SpotifyApiSource()
        self._web_source = SpotifyWebSource()

    def get_active_source_info(self) -> Dict[str, Any]:
        api_test = self._api_source.test_connection()
        snapshot_meta = self._export_source.get_metadata()
        
        return {
            "active_mode": self.source_type,
            "api_available": api_test.get("connected", False),
            "api_state": self._api_source.connection_state.value,
            "snapshot_loaded": snapshot_meta is not None,
            "snapshot_metadata": snapshot_meta.model_dump() if snapshot_meta else None
        }

    def get_source_delegate(self, source_type: Optional[str] = None, file_path: Optional[str] = None) -> MusicSource:
        mode = source_type or self.source_type
        if mode == "api" and self._api_source.is_available():
            return self._api_source
        elif mode == "web":
            return self._web_source
        else:
            p = Path(file_path) if file_path else self.file_path
            return SpotifyExportSource(file_path=p)

    def get_playlists(self, source_type: Optional[str] = None, file_path: Optional[str] = None) -> List[Playlist]:
        mode = source_type or self.source_type
        if mode == "api":
            try:
                if self._api_source.is_available():
                    pls = self._api_source.get_playlists()
                    if pls:
                        return pls
            except Exception as e:
                logger.warning(f"Spotify API playlists query failed: {e}")
            
            # Intelligent Fallback: If API mode returned 0 playlists (e.g. 403 Developer restrictions),
            # automatically fallback to snapshot data if available so the user isn't stranded.
            snap_pls = self._export_source.get_playlists()
            if snap_pls:
                logger.info(f"Spotify API returned 0 playlists. Automatically fell back to {len(snap_pls)} snapshot playlists.")
                return snap_pls
            return []

        return self._export_source.get_playlists()

    def get_playlist_tracks(self, playlist_id_or_url: str, source_type: Optional[str] = None, file_path: Optional[str] = None) -> Playlist:
        mode = source_type or self.source_type
        if mode == "api" or ("open.spotify.com/playlist" in playlist_id_or_url and mode != "export"):
            try:
                if self._api_source.is_available():
                    pl = self._api_source.get_playlist_tracks(playlist_id_or_url)
                    if pl and pl.tracks:
                        return pl
            except Exception as e:
                logger.warning(f"Spotify API track fetch failed: {e}")

            # Try public web scraper if URL is provided
            try:
                if "open.spotify.com/playlist" in playlist_id_or_url or "spotify:playlist:" in playlist_id_or_url:
                    pl = self._web_source.get_playlist_tracks(playlist_id_or_url)
                    if pl and pl.tracks:
                        return pl
            except Exception as e:
                logger.warning(f"Web scraper track fetch failed: {e}")

        p = Path(file_path) if file_path else self.file_path
        return self._export_source.get_playlist_tracks(playlist_id_or_url)


    def get_liked_tracks(self, source_type: Optional[str] = None, file_path: Optional[str] = None) -> List[Track]:
        mode = source_type or self.source_type
        if mode == "api" and self._api_source.is_available():
            try:
                return self._api_source.get_liked_tracks()
            except Exception:
                pass
        return self._export_source.get_liked_tracks()
