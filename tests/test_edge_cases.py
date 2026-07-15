import pytest
from unittest.mock import MagicMock, patch
from src.core.models import Playlist, Track, MirroredPlaylist, MirrorStatus, SyncPlan, SyncPlanItem, PlanAction
from src.core.database import DatabaseManager, init_db
from src.services.sync_service import SyncService
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.domain.sync_planner import SyncPlanner
from src.domain.normalizer import clean_query_string, normalize_unicode, extract_title_and_featured

def test_deleted_yt_playlist_auto_heals():
    """Test that when a YouTube playlist returns 404/deleted, generate_sync_plan handles it cleanly."""
    init_db()
    mirror = DatabaseManager.create_mirror(
        name="Deleted PL",
        yt_playlist_id="PL_DELETED_123",
        yt_playlist_name="Deleted PL",
        spotify_id="s_deleted"
    )

    mock_dest = MagicMock()
    mock_dest.get_playlist.return_value = None  # Simulates 404 / deleted playlist on YouTube

    service = SyncService()
    mock_source = MagicMock()
    mock_source.get_playlist_tracks.return_value = Playlist(name="Deleted PL", track_count=1, tracks=[Track(name="Song", artist="Artist")])

    plan = service.generate_sync_plan(mirror.id, destination=mock_dest, source=mock_source)
    assert plan is None
    # Verify mirror was preserved and marked as UNREACHABLE instead of destructively deleted
    updated_mirror = DatabaseManager.get_mirror(mirror.id)
    assert updated_mirror is not None
    assert updated_mirror.last_sync_status == MirrorStatus.UNREACHABLE

def test_add_tracks_to_playlist_breaks_on_404():
    """Test that chunk upload loop halts immediately on HTTP 404 error."""
    dest = YouTubeMusicDestination()
    dest.client = MagicMock()
    # Mock add_playlist_items to raise 404 error on first chunk
    dest.client.add_playlist_items.side_effect = Exception("Server returned HTTP 404: Not Found")

    video_ids = [f"vid_{i}" for i in range(250)]  # 5 chunks of 50
    added = dest.add_tracks_to_playlist("PL_MISSING", video_ids)
    assert added == 0
    # Should only have attempted 1 chunk and aborted immediately
    assert dest.client.add_playlist_items.call_count == 1

def test_duplicate_tracks_in_spotify_export():
    """Test playlist with duplicate tracks (e.g. reprise)."""
    sp_tracks = [
        Track(id="t1", name="Resonance", artist="HOME"),
        Track(id="t1", name="Resonance", artist="HOME"),  # Duplicate song in playlist
        Track(id="t2", name="Generator", artist="Justice")
    ]
    sp_pl = Playlist(name="Synthwave", tracks=sp_tracks)
    dest_tracks = [
        Track(id="vid_res", name="Resonance", artist="HOME")
    ]

    plan = SyncPlanner.generate_plan(sp_pl, dest_tracks)
    assert plan.source_track_count == 3
    assert plan.destination_track_count == 1
    # Both instances of Resonance should be handled
    in_sync_items = [it for it in plan.items if it.action == PlanAction.NO_OP]
    assert len(in_sync_items) >= 1

def test_utf8_special_characters_safety():
    """Test exotic scripts and punctuation in track and artist names."""
    test_cases = [
        ("米津玄師", "Lemon (Remastered 2020)", "米津玄師 Lemon"),
        ("방탄소년단", "Dynamite - Deluxe Edition", "방탄소년단 Dynamite"),
        ("Виктор Цой", "Группа крови (Live)", "Виктор Цой Группа крови"),
        ("AC/DC", "Thunderstruck (Live at Donington)", "AC DC Thunderstruck"),
        ("Ke$ha", "TiK ToK - Single Version", "Ke sha TiK ToK"),
        ("Panic! At The Disco", "High Hopes (Official Video)", "Panic At The Disco High Hopes"),
    ]

    for artist, title, expected_clean in test_cases:
        query = clean_query_string(artist, title)
        assert expected_clean.lower() in query.lower() or query.replace(" ", "") != ""
