import pytest
from src.core.models import Track, CandidateTrack, ConfidenceTier
from src.domain.matcher import MatchingEngine

def test_exact_match():
    source = Track(name="Bohemian Rhapsody", artist="Queen", duration_seconds=354.0)
    candidate = CandidateTrack(
        video_id="fJ9rUzIMcZQ",
        title="Bohemian Rhapsody",
        artist="Queen",
        duration_seconds=355.0,
        result_type="song"
    )

    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_tier == ConfidenceTier.EXACT
    assert result.confidence_score >= 0.92
    assert result.matched_track.video_id == "fJ9rUzIMcZQ"
    assert result.is_accepted is True

def test_remaster_high_match():
    source = Track(name="Come Together - Remastered 2009", artist="The Beatles", duration_seconds=259.0)
    candidate = CandidateTrack(
        video_id="45cYwDMibGo",
        title="Come Together",
        artist="The Beatles",
        duration_seconds=260.0,
        result_type="song"
    )

    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_tier in [ConfidenceTier.EXACT, ConfidenceTier.HIGH]
    assert result.confidence_score >= 0.80
    assert "The Beatles" in result.matched_track.artist

def test_live_mismatch_penalty():
    # Studio source vs Live candidate -> Should trigger severe penalty
    source = Track(name="Hotel California", artist="Eagles", duration_seconds=390.0)
    candidate = CandidateTrack(
        video_id="live123",
        title="Hotel California (Live at The Forum)",
        artist="Eagles",
        duration_seconds=420.0,
        result_type="song"
    )

    result = MatchingEngine.match_track(source, [candidate])
    # The penalty should lower confidence substantially
    assert result.confidence_score < 0.65
    assert result.confidence_tier in [ConfidenceTier.AMBIGUOUS, ConfidenceTier.NO_MATCH]
    assert "Live" in result.rationale

def test_remix_mismatch_penalty():
    # Studio original vs Remix candidate
    source = Track(name="Blinding Lights", artist="The Weeknd", duration_seconds=200.0)
    candidate = CandidateTrack(
        video_id="remix123",
        title="Blinding Lights (Major Lazer Remix)",
        artist="The Weeknd",
        duration_seconds=195.0,
        result_type="song"
    )

    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score < 0.65
    assert result.confidence_tier in [ConfidenceTier.AMBIGUOUS, ConfidenceTier.NO_MATCH]

def test_duration_penalty():
    source = Track(name="Short Song", artist="Some Artist", duration_seconds=120.0)
    candidate = CandidateTrack(
        video_id="12345",
        title="Short Song",
        artist="Some Artist",
        duration_seconds=600.0,  # 10 minute extended version
        result_type="video"
    )

    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score < 0.80
    assert result.confidence_tier in [ConfidenceTier.PROBABLE, ConfidenceTier.AMBIGUOUS, ConfidenceTier.NO_MATCH]

def test_no_candidates():
    source = Track(name="Nonexistent Song", artist="Unknown Artist")
    result = MatchingEngine.match_track(source, [])
    assert result.confidence_tier == ConfidenceTier.NO_MATCH
    assert result.matched_track is None
    assert result.is_accepted is False
