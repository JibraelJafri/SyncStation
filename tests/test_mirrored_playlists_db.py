import pytest
from src.core.database import DatabaseManager, init_db, get_db
from src.core.models import (
    MirroredPlaylist,
    MirrorTrackManifest,
    MirrorStatus,
    Track,
    CandidateTrack,
    ConfidenceTier
)

def test_database_and_mirrors_crud():
    init_db()

    # 1. Create Mirror
    mirror = DatabaseManager.create_mirror(
        name="Test Rock Playlist",
        yt_playlist_id="PLrock123",
        yt_playlist_name="Test Rock Playlist",
        spotify_id="sp_rock_id",
        spotify_track_count=150,
        yt_track_count=150
    )
    assert mirror is not None
    assert mirror.name == "Test Rock Playlist"
    assert mirror.yt_playlist_id == "PLrock123"

    # 2. Get Mirror
    fetched = DatabaseManager.get_mirror(mirror.id)
    assert fetched is not None
    assert fetched.id == mirror.id

    # 3. Save Manifest Tracks
    DatabaseManager.save_manifest_tracks(mirror.id, [
        {
            "spotify_uri": "spotify:track:rock1",
            "spotify_name": "Rock Song 1",
            "spotify_artist": "Rock Band",
            "spotify_album": "Rock Album",
            "spotify_duration": 210.0,
            "yt_video_id": "yt_rock1",
            "yt_title": "Rock Song 1",
            "yt_artist": "Rock Band"
        },
        {
            "spotify_uri": "spotify:track:rock2",
            "spotify_name": "Rock Song 2",
            "spotify_artist": "Rock Band",
            "spotify_album": "Rock Album",
            "spotify_duration": 180.0,
            "yt_video_id": "yt_rock2",
            "yt_title": "Rock Song 2",
            "yt_artist": "Rock Band"
        }
    ])

    manifest = DatabaseManager.get_manifest_tracks(mirror.id)
    assert len(manifest) == 2
    assert manifest[0].spotify_name == "Rock Song 1"

    # 4. Update Mirror Delta
    DatabaseManager.update_mirror_status(
        mirror.id,
        status="CHANGES_DETECTED",
        spotify_count=155,
        delta_added=5
    )
    updated = DatabaseManager.get_mirror(mirror.id)
    assert updated.last_sync_status == MirrorStatus.CHANGES_DETECTED
    assert updated.delta_added_count == 5

    # 5. Clean up
    DatabaseManager.delete_mirror(mirror.id)
    assert DatabaseManager.get_mirror(mirror.id) is None
