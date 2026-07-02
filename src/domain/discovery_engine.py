from typing import List, Dict, Any, Optional
from rapidfuzz import fuzz

from src.core.models import (
    Playlist,
    Track,
    MirroredPlaylist,
    MirrorDiscoveryCandidate
)
from src.domain.normalizer import clean_query_string

class MirrorDiscoveryEngine:
    @staticmethod
    def calculate_name_similarity(name1: str, name2: str) -> float:
        if not name1 or not name2:
            return 0.0
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()
        ratio = fuzz.ratio(n1, n2) / 100.0
        token_ratio = fuzz.token_sort_ratio(n1, n2) / 100.0
        return max(ratio, token_ratio)

    @classmethod
    def evaluate_mirror_candidate(
        cls,
        spotify_playlist: Playlist,
        yt_playlist_id: str,
        yt_playlist_name: str,
        yt_tracks: List[Track],
        existing_mirror_id: Optional[str] = None
    ) -> MirrorDiscoveryCandidate:
        name_sim = cls.calculate_name_similarity(spotify_playlist.name, yt_playlist_name)
        
        # Calculate track overlap using clean query strings
        sp_queries = {clean_query_string(t.artist, t.name).lower() for t in spotify_playlist.tracks if t.name}
        yt_queries = {clean_query_string(t.artist, t.name).lower() for t in yt_tracks if t.name}

        overlap_count = 0
        for sq in sp_queries:
            if sq in yt_queries:
                overlap_count += 1
            else:
                # Fuzzy token match for slight naming variances
                for yq in yt_queries:
                    if fuzz.ratio(sq, yq) >= 88:
                        overlap_count += 1
                        break

        total_sp_tracks = max(1, len(spotify_playlist.tracks))
        overlap_percent = round((overlap_count / total_sp_tracks) * 100, 1)

        # Composite confidence
        overall_confidence = (name_sim * 0.35) + ((overlap_percent / 100.0) * 0.65)
        overall_confidence = round(min(1.0, max(0.0, overall_confidence)), 3)

        return MirrorDiscoveryCandidate(
            spotify_playlist=spotify_playlist,
            yt_playlist_id=yt_playlist_id,
            yt_playlist_name=yt_playlist_name,
            name_similarity=round(name_sim, 3),
            track_overlap_count=overlap_count,
            track_total_count=len(spotify_playlist.tracks),
            track_overlap_percent=overlap_percent,
            overall_confidence=overall_confidence,
            existing_mirror_id=existing_mirror_id,
            is_linked=bool(existing_mirror_id)
        )
