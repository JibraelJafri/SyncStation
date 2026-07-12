from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from src.core.models import Playlist, Track, CandidateTrack

class MusicSource(ABC):
    @abstractmethod
    def get_playlists(self) -> List[Playlist]:
        """List all available playlists from this source."""
        pass

    @abstractmethod
    def get_playlist_tracks(self, playlist_id_or_url: str) -> Playlist:
        """Fetch all tracks for a specific playlist."""
        pass

    @abstractmethod
    def get_liked_tracks(self) -> List[Track]:
        """Fetch user liked/saved tracks if supported."""
        pass

class MusicDestination(ABC):
    @abstractmethod
    def search_candidates(self, track: Track, limit: int = 5) -> List[CandidateTrack]:
        """Search destination catalog for potential matching tracks."""
        pass

    @abstractmethod
    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        """Fetch destination playlist and existing tracks."""
        pass

    @abstractmethod
    def create_playlist(self, name: str, description: str, privacy: str = "PRIVATE", video_ids: List[str] = None) -> str:
        """Create a new playlist and return its ID."""
        pass

    @abstractmethod
    def add_tracks_to_playlist(self, playlist_id: str, video_ids: List[str]) -> int:
        """Add video IDs to an existing playlist and return count added."""
        pass

    @abstractmethod
    def rate_track(self, video_id: str, rating: str = "LIKE") -> bool:
        """Rate/like a track on destination."""
        pass

    def remove_tracks_from_playlist(self, playlist_id: str, video_ids: Any) -> int:
        """Remove tracks from playlist on destination."""
        return 0
