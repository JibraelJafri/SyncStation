import uuid
from unittest.mock import MagicMock
from src.core.database import init_db, DatabaseManager, get_db
from src.core.models import MirroredPlaylist, MirrorStatus
from src.services.review_service import ReviewService

def test_apply_and_push_override_flow():
    init_db()
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    sp_id = f"sp_{uuid.uuid4().hex[:8]}"
    dz_id = f"dz_{uuid.uuid4().hex[:8]}"

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO sync_jobs (id, playlist_name, spotify_url_or_id, mode, destination, status, total_tracks, synced_tracks)
        VALUES (?, 'Review List', ?, 'DELTA', 'deezer', 'PARTIAL_SUCCESS', 5, 4)
        """, (job_id, sp_id))

        cur.execute("""
        INSERT INTO sync_items (job_id, track_index, source_name, source_artist, source_uri, is_accepted)
        VALUES (?, 0, 'Misspelled Track', 'Artist', 'spotify:track:123', 0)
        """, (job_id,))
        item_id = cur.lastrowid

    # Create mirror outside the conn context
    DatabaseManager.create_mirror(
        name="Review List",
        spotify_id=sp_id,
        yt_playlist_id=dz_id,
        yt_playlist_name="Review List",
        destination="deezer",
        spotify_track_count=5,
        yt_track_count=4
    )

    mock_dest = MagicMock()
    mock_dest.add_tracks_to_playlist.return_value = 1

    res = ReviewService.apply_and_push_override(
        item_id=item_id,
        video_id="999888",
        title="Correct Track",
        artist="Artist",
        destination=mock_dest,
        destination_playlist_id=dz_id
    )

    assert res.get("success") is True
    assert res.get("pushed") is True
    assert mock_dest.add_tracks_to_playlist.called

    # Check manifest updated
    mirror = DatabaseManager.get_mirror_by_spotify(sp_id, destination="deezer")
    assert mirror is not None
    manifest = DatabaseManager.get_manifest_tracks(mirror.id)
    assert len(manifest) == 1
    assert manifest[0].yt_video_id == "999888"

    # Check mirror track count bumped
    updated_mirror = DatabaseManager.get_mirror(mirror.id)
    assert updated_mirror.yt_track_count == 5
