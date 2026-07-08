from typing import Optional
from src.core.models import Track, CandidateTrack, MatchResult, ConfidenceTier
from src.core.database import DatabaseManager
from src.domain.normalizer import clean_query_string

class OverrideManager:
    @staticmethod
    def get_override_match(track: Track) -> Optional[MatchResult]:
        query = clean_query_string(track.artist, track.name)
        override = DatabaseManager.get_override(query, track.uri)
        if override:
            candidate = CandidateTrack(
                video_id=override["target_video_id"],
                title=override.get("target_title", track.name),
                artist=override.get("target_artist", track.artist),
                album="",
                duration_seconds=track.duration_seconds,
                result_type="song"
            )
            return MatchResult(
                source_track=track,
                matched_track=candidate,
                confidence_score=1.0,
                confidence_tier=ConfidenceTier.EXACT,
                rationale="User manual override applied",
                alternative_candidates=[],
                is_manual_override=True,
                is_accepted=True
            )
        return None

    @staticmethod
    def set_override(track: Track, candidate: CandidateTrack):
        query = clean_query_string(track.artist, track.name)
        DatabaseManager.save_override(
            query=query,
            video_id=candidate.video_id,
            title=candidate.title,
            artist=candidate.artist,
            uri=track.uri
        )
