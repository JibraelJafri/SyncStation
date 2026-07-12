import pytest
from src.core.models import Track, CandidateTrack, ConfidenceTier
from src.domain.matcher import MatchingEngine

def test_subset_title_penalty_run_vs_run_for_your_life():
    """Verify single-word / subset titles are not falsely matched to longer songs."""
    source = Track(name="Run", artist="Joji", duration_seconds=195.0)
    wrong_candidate = CandidateTrack(
        video_id="wrong123",
        title="Run For Your Life",
        artist="Joji",
        duration_seconds=195.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [wrong_candidate])
    # Must NOT be EXACT or HIGH; must be rejected or marked AMBIGUOUS
    assert result.confidence_tier in [ConfidenceTier.AMBIGUOUS, ConfidenceTier.NO_MATCH]
    assert result.is_accepted is False
    assert result.confidence_score < 0.70

def test_stay_vs_stay_with_me():
    """Verify subset 'Stay' vs 'Stay with Me' is not auto-accepted."""
    source = Track(name="Stay", artist="Post Malone", duration_seconds=204.0)
    candidate = CandidateTrack(
        video_id="stay_with_me",
        title="Stay with Me",
        artist="Post Malone",
        duration_seconds=205.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [candidate])
    assert result.is_accepted is False
    assert result.confidence_tier in [ConfidenceTier.AMBIGUOUS, ConfidenceTier.NO_MATCH]

def test_short_artist_boundary_cher_vs_her():
    """Verify short artist names (e.g. HER) do not match longer names containing those letters (e.g. Cher)."""
    source = Track(name="Damage", artist="H.E.R.", duration_seconds=220.0)
    candidate = CandidateTrack(
        video_id="cher_track",
        title="Damage",
        artist="Cher",
        duration_seconds=220.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [candidate])
    # Artist matching should fail between H.E.R. and Cher
    assert result.confidence_score < 0.70
    assert result.is_accepted is False

def test_short_artist_boundary_linkin_park_vs_in():
    """Verify 2-letter tokens like 'in' do not match 'Linkin Park'."""
    source = Track(name="Papercut", artist="In", duration_seconds=184.0)
    candidate = CandidateTrack(
        video_id="lp_track",
        title="Papercut",
        artist="Linkin Park",
        duration_seconds=184.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score < 0.70
    assert result.is_accepted is False

def test_movement_part_mismatch_penalty():
    """Verify Part 1 vs Part 2 triggers a severe movement mismatch penalty."""
    source = Track(name="Another Brick in the Wall, Pt. 1", artist="Pink Floyd", duration_seconds=190.0)
    candidate = CandidateTrack(
        video_id="brick_pt2",
        title="Another Brick in the Wall, Pt. 2",
        artist="Pink Floyd",
        duration_seconds=239.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score < 0.50
    assert result.confidence_tier in [ConfidenceTier.AMBIGUOUS, ConfidenceTier.NO_MATCH]
    assert "Part mismatch" in result.rationale

def test_cover_version_penalty():
    """Verify studio originals are protected against user-uploaded covers."""
    source = Track(name="Rolling in the Deep", artist="Adele", duration_seconds=228.0)
    candidate = CandidateTrack(
        video_id="cover_vid",
        title="Rolling in the Deep (Cover by Boyce Avenue)",
        artist="Boyce Avenue",
        duration_seconds=230.0,
        result_type="video"
    )
    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score < 0.55
    assert result.is_accepted is False
    assert "Cover" in result.rationale

def test_sped_up_nightcore_penalty():
    """Verify Sped Up / Nightcore modifications are penalized."""
    source = Track(name="Escapism", artist="RAYE", duration_seconds=272.0)
    candidate = CandidateTrack(
        video_id="sped_vid",
        title="Escapism (Sped Up)",
        artist="RAYE",
        duration_seconds=230.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score < 0.65
    assert result.is_accepted is False
    assert "Sped Up" in result.rationale

def test_isrc_exact_match():
    """Verify ISRC matching provides 100% confidence for identical master recordings."""
    class ISRCCandidate(CandidateTrack):
        isrc: str = ""

    source = Track(name="Bohemian Rhapsody", artist="Queen", duration_seconds=354.0, isrc="GBUM71029604")
    candidate = ISRCCandidate(
        video_id="fJ9rUzIMcZQ",
        title="Bohemian Rhapsody",
        artist="Queen",
        duration_seconds=355.0,
        result_type="song",
        isrc="GBUM71029604"
    )
    result = MatchingEngine.match_track(source, [candidate])
    assert result.confidence_score == 1.0
    assert result.confidence_tier == ConfidenceTier.EXACT
    assert result.is_accepted is True
    assert "Exact ISRC match" in result.rationale

def test_ascii_safe_rationale():
    """Verify that rationale strings are ASCII-safe without greek delta characters."""
    source = Track(name="Song A", artist="Artist A", duration_seconds=180.0)
    candidate = CandidateTrack(
        video_id="vid1",
        title="Song A",
        artist="Artist A",
        duration_seconds=182.0,
        result_type="song"
    )
    result = MatchingEngine.match_track(source, [candidate])
    # Must be encodable as ascii / cp1252 without UnicodeEncodeError
    result.rationale.encode("ascii")
    assert "diff=" in result.rationale
