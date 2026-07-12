import time
from typing import Dict, Any, Optional, List, Type
from src.core.database import DatabaseManager
from src.providers.base import MusicDestination
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.providers.deezer.deezer_dest import DeezerDestination

class DestinationRegistry:
    """
    Pluggable registry for music destinations (YouTube Music, Deezer).
    Allows seamless switching of sync destination via CLI flag or TUI preferences.
    Features cached connection status diagnostics (30s TTL) to prevent UI lag.
    """

    _destinations: Dict[str, Type[MusicDestination]] = {
        "youtube": YouTubeMusicDestination,
        "ytmusic": YouTubeMusicDestination,
        "deezer": DeezerDestination
    }

    _cached_statuses: Optional[Dict[str, Dict[str, Any]]] = None
    _statuses_timestamp: float = 0.0
    _CACHE_TTL_SECONDS: float = 300.0

    @classmethod
    def get_active_destination_name(cls) -> str:
        """Returns the configured active destination name ('youtube' or 'deezer')."""
        return DatabaseManager.get_app_preference("active_destination", "youtube").lower()

    @classmethod
    def set_active_destination_name(cls, name: str) -> None:
        """Sets the default destination preference."""
        clean = name.strip().lower()
        if clean not in cls._destinations:
            raise ValueError(f"Unknown destination: '{name}'. Available: {list(cls.list_destinations())}")
        canonical = "youtube" if clean in ["youtube", "ytmusic"] else "deezer"
        DatabaseManager.set_app_preference("active_destination", canonical)

    @classmethod
    def list_destinations(cls) -> List[str]:
        """Returns unique list of supported destinations."""
        return ["youtube", "deezer"]

    @classmethod
    def get_destination(cls, name: Optional[str] = None) -> MusicDestination:
        """
        Instantiates and returns the requested destination (or default active destination).
        """
        target = (name or cls.get_active_destination_name()).strip().lower()
        if target in ["deezer"]:
            return DeezerDestination()
        return YouTubeMusicDestination()

    @classmethod
    def invalidate_status_cache(cls):
        """Clears the cached status diagnostics to force immediate fresh checks."""
        cls._cached_statuses = None
        cls._statuses_timestamp = 0.0

    @classmethod
    def get_all_statuses(cls, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        Returns connection diagnostics for all registered destinations.
        Uses in-memory caching with a 30-second TTL to avoid blocking terminal renders.
        """
        now = time.time()
        if not force_refresh and cls._cached_statuses is not None:
            if (now - cls._statuses_timestamp) < cls._CACHE_TTL_SECONDS:
                return cls._cached_statuses

        statuses = {}

        # YouTube Music Status
        try:
            yt = YouTubeMusicDestination()
            statuses["youtube"] = yt.test_connection()
        except Exception as e:
            statuses["youtube"] = {"connected": False, "message": str(e)}

        # Deezer Status
        try:
            dz = DeezerDestination()
            statuses["deezer"] = dz.test_connection()
        except Exception as e:
            statuses["deezer"] = {"connected": False, "message": str(e)}

        cls._cached_statuses = statuses
        cls._statuses_timestamp = now
        return statuses
