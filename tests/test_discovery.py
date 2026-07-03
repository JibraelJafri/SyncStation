import pytest
from src.core.models import Playlist, Track
from src.domain.discovery_engine import MirrorDiscoveryEngine

def test_mirror_discovery_high_overlap():
    sp_pl = Playlist(
        name="My 3K Playlist",
        tracks=[
            Track(name="Song 1", artist="Artist A"),
            Track(name="Song 2", artist="Artist B"),
            Track(name="Song 3", artist="Artist C"),
            Track(name="Song 4", artist="Artist D")
        ]
    )

    yt_tracks = [
        Track(id="yt1", name="Song 1", artist="Artist A"),
        Track(id="yt2", name="Song 2", artist="Artist B"),
        Track(id="yt3", name="Song 3", artist="Artist C"),
        Track(id="yt4", name="Song 4", artist="Artist D")
    ]

    cand = MirrorDiscoveryEngine.evaluate_mirror_candidate(
        spotify_playlist=sp_pl,
        yt_playlist_id="PL12345",
        yt_playlist_name="My 3K Playlist",
        yt_tracks=yt_tracks
    )

    assert cand.name_similarity == 1.0
    assert cand.track_overlap_count == 4
    assert cand.track_overlap_percent == 100.0
    assert cand.overall_confidence >= 0.95

def test_mirror_discovery_partial_match():
    sp_pl = Playlist(
        name="Workout Bangers 2026",
        tracks=[
            Track(name="Heavy Bass", artist="DJ Pro"),
            Track(name="Speed Run", artist="Runner"),
            Track(name="Push It", artist="Gym Star")
        ]
    )

    yt_tracks = [
        Track(id="yt1", name="Heavy Bass", artist="DJ Pro"),
        Track(id="yt2", name="Speed Run", artist="Runner"),
        Track(id="yt99", name="Unrelated Song", artist="Unknown")
    ]

    cand = MirrorDiscoveryEngine.evaluate_mirror_candidate(
        spotify_playlist=sp_pl,
        yt_playlist_id="PLworkout",
        yt_playlist_name="Workout Bangers",
        yt_tracks=yt_tracks
    )

    assert cand.name_similarity >= 0.80
    assert cand.track_overlap_count == 2
    assert cand.overall_confidence >= 0.65
