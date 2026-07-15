import pytest
from unittest.mock import MagicMock, patch
from src.core.models import (
    Playlist,
    Track,
    CandidateTrack,
    SyncMode,
    JobStatus,
    ConfidenceTier,
    MirrorStatus,
    TransferOutcome,
    TrackDiscrepancy
)
from src.core.database import init_db, DatabaseManager, get_db
from src.services.sync_service import SyncService
from src.services.review_service import ReviewService
from src.providers.base import MusicSource, MusicDestination

class FakeSource(MusicSource):
    def __init__(self, tracks, name="Test Playlist"):
        self._tracks = tracks
        self._name = name

    def get_playlists(self):
        return [Playlist(id="fake_pl", name=self._name, track_count=len(self._tracks), tracks=self._tracks)]

    def get_playlist_tracks(self, identifier):
        return Playlist(id="fake_pl", name=self._name, track_count=len(self._tracks), tracks=self._tracks)

    def get_liked_tracks(self):
        return self._tracks

class PartialMockDestination(MusicDestination):
    def __init__(self, matchable_count=96):
        self.matchable_count = matchable_count
        self.created_playlists = {}
        self.playlist_items = {}
        self.create_call_count = 0

    def is_available(self):
        return True

    def search_candidates(self, track: Track, limit: int = 5):
        # Extract track index from track name "Track XX"
        try:
            idx = int(track.name.split()[1])
        except Exception:
            idx = 1

        if idx <= self.matchable_count:
            return [CandidateTrack(
                video_id=f"vid_{idx}",
                title=track.name,
                artist=track.artist,
                duration_seconds=track.duration_seconds,
                result_type="song"
            )]
        elif idx <= self.matchable_count + 2:
            # Ambiguous match (acoustic variance with 0.70 penalty lands ~0.57 in AMBIGUOUS tier)
            return [CandidateTrack(
                video_id=f"vid_{idx}_ambiguous",
                title=f"{track.name} (Acoustic)",
                artist=track.artist,
                duration_seconds=track.duration_seconds + 10.0,
                result_type="song"
            )]
        else:
            # Zero candidates found
            return []

    def get_playlist(self, playlist_id: str):
        if playlist_id not in self.created_playlists:
            return None
        tracks = [Track(id=vid, name=f"Song {vid}", artist="Artist") for vid in self.playlist_items.get(playlist_id, [])]
        return Playlist(id=playlist_id, name=self.created_playlists[playlist_id], tracks=tracks)

    def create_playlist(self, name: str, description: str = "", privacy: str = "PRIVATE", video_ids=None):
        self.create_call_count += 1
        pl_id = f"pl_{name.replace(' ', '_').lower()}"
        self.created_playlists[pl_id] = name
        self.playlist_items[pl_id] = list(video_ids or [])
        return pl_id

    def add_tracks_to_playlist(self, playlist_id: str, video_ids):
        if playlist_id not in self.playlist_items:
            self.playlist_items[playlist_id] = []
        self.playlist_items[playlist_id].extend(video_ids)
        return len(video_ids)

    def remove_tracks_from_playlist(self, playlist_id: str, tracks):
        return len(tracks)

    def rate_track(self, video_id: str, rating: str = "LIKE"):
        return True

def test_96_of_100_partial_success_accounting():
    """UX Invariant 1: Honest tri-state transfer outcome and accurate track accounting."""
    init_db()
    source_tracks = [
        Track(
            id=f"s_{i}",
            uri=f"spotify:track:s_{i}",
            name=f"Track {i}",
            artist=f"Artist {i}",
            duration_seconds=180.0
        )
        for i in range(1, 101)
    ]
    source = FakeSource(source_tracks, name="Century Playlist")
    dest = PartialMockDestination(matchable_count=96)
    svc = SyncService()

    # 1. Analyze
    job = svc.analyze_for_transfer(source, "Century Playlist", destination=dest)
    assert job.status == JobStatus.AWAITING_REVIEW
    assert job.total_tracks == 100
    assert job.matched_tracks == 96

    # 2. Execute Transfer
    report = svc.execute_transfer(job.id, destination=dest)

    # 3. Assert honest outcome and strict ledger accounting
    assert report.status == "PARTIAL_SUCCESS"
    assert report.outcome == TransferOutcome.PARTIAL_SUCCESS
    assert report.is_complete is False
    assert report.total_source_tracks == 100
    assert report.total_synced_tracks == 96
    assert report.unmatched_tracks == 4
    assert len(report.discrepancies) == 4
    assert report.discrepancy_count == 4

    # Check that the 4 discrepancies accurately describe what was omitted
    discrepancy_types = [d.discrepancy_type for d in report.discrepancies]
    assert "AMBIGUOUS" in discrepancy_types
    assert "UNMATCHED" in discrepancy_types

    # Ensure the destination playlist actually contains exactly 96 tracks
    dest_pl = dest.get_playlist(report.yt_playlist_url.split("list=")[-1])
    assert len(dest_pl.tracks) == 96

def test_resume_is_strictly_idempotent():
    """UX Invariant 3: Resume must never duplicate playlist or tracks on destination."""
    init_db()
    source_tracks = [
        Track(id=f"t_{i}", uri=f"spotify:track:t_{i}", name=f"Track {i}", artist="Artist", duration_seconds=180.0)
        for i in range(1, 81)
    ]
    source = FakeSource(source_tracks, name="Resume Test")
    dest = PartialMockDestination(matchable_count=80)
    svc = SyncService()

    job = svc.analyze_for_transfer(source, "Resume Test", destination=dest)
    
    # Simulate an interrupted job where initial 50 tracks were uploaded to pl_resume_test
    pl_id = dest.create_playlist("Resume Test", video_ids=[f"vid_{i}" for i in range(1, 51)])
    job.yt_playlist_id = pl_id
    job.synced_tracks = 50
    DatabaseManager.update_job_status(job.id, JobStatus.PAUSED.value, yt_playlist_id=pl_id, synced_count=50)

    # Reset creation counter
    dest.create_call_count = 1

    # Resume the transfer
    report = svc.execute_transfer(job.id, destination=dest)

    # Verify create_playlist was NOT called again
    assert dest.create_call_count == 1

    # Verify all 80 tracks are now present on destination with 0 duplicates
    dest_pl = dest.get_playlist(pl_id)
    assert len(dest_pl.tracks) == 80
    assert len(set(t.id for t in dest_pl.tracks)) == 80

    assert report.status == "COMPLETED"
    assert report.total_synced_tracks == 80

def test_manual_override_live_propagation_and_manifest():
    """UX Invariant 4: Manual overrides must push to destination and update manifests."""
    init_db()
    dest = PartialMockDestination(matchable_count=10)
    pl_id = dest.create_playlist("Override Test", video_ids=["vid_1", "vid_2"])
    
    mirror = DatabaseManager.create_mirror(
        name="Override Test",
        destination="youtube",
        yt_playlist_id=pl_id,
        yt_playlist_name="Override Test",
        spotify_id="sp_override",
        spotify_track_count=3,
        yt_track_count=2
    )

    # Create job with 1 unaccepted item
    job_id = "job_override_test"
    DatabaseManager.create_job(
        job_id=job_id,
        playlist_name="Override Test",
        spotify_url="sp_override",
        mode="TRANSFER",
        total_tracks=3,
        mirror_id=mirror.id,
        yt_playlist_id=pl_id,
        destination="youtube"
    )

    with get_db() as conn:
        conn.execute("""
        INSERT INTO sync_items (
            job_id, track_index, source_name, source_artist, source_album, source_duration,
            source_uri, matched_video_id, matched_title, matched_artist, confidence_score,
            confidence_tier, rationale, status, is_manual_override, is_accepted, alternatives_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, 3, "Missing Song", "Indie Artist", "EP", 210.0,
            "spotify:track:missing_3", None, None, None, 0.0,
            "NO_MATCH", "No candidate found", "SKIPPED", 0, 0, "[]"
        ))
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Apply override with live push
    res = ReviewService.apply_and_push_override(
        item_id=item_id,
        video_id="vid_resolved_3",
        title="Missing Song (Official)",
        artist="Indie Artist",
        destination=dest,
        destination_playlist_id=pl_id
    )

    assert res["success"] is True
    assert res["pushed"] is True
    assert "vid_resolved_3" in dest.playlist_items[pl_id]

    # Verify manifest was populated
    manifest = DatabaseManager.get_manifest_tracks(mirror.id)
    assert any(m.yt_video_id == "vid_resolved_3" for m in manifest)

    # Verify mirror track count incremented
    updated_mirror = DatabaseManager.get_mirror(mirror.id)
    assert updated_mirror.yt_track_count == 3

def test_transient_error_preserves_mirror_and_manifest():
    """UX Invariant 5: Transient destination failures must never delete local history."""
    init_db()
    mirror = DatabaseManager.create_mirror(
        name="Important Playlist",
        destination="youtube",
        yt_playlist_id="PL_TRANSIENT_404",
        yt_playlist_name="Important Playlist",
        spotify_id="sp_important",
        spotify_track_count=50,
        yt_track_count=50
    )

    DatabaseManager.save_manifest_tracks(mirror.id, [{
        "spotify_uri": "spotify:track:imp1",
        "spotify_name": "Song 1",
        "spotify_artist": "Artist 1",
        "spotify_album": "Album",
        "spotify_duration": 180.0,
        "yt_video_id": "vid_imp1",
        "yt_title": "Song 1",
        "yt_artist": "Artist 1"
    }])

    # Destination returns None (simulating temporary network failure or API 404)
    mock_dest = MagicMock()
    mock_dest.get_playlist.return_value = None

    svc = SyncService()
    plan = svc.generate_sync_plan(mirror.id, destination=mock_dest, source=FakeSource([]))

    assert plan is None

    # Verify mirror was NOT deleted from the database
    persisted_mirror = DatabaseManager.get_mirror(mirror.id)
    assert persisted_mirror is not None
    assert persisted_mirror.last_sync_status == MirrorStatus.UNREACHABLE

    # Verify manifest tracks remain intact
    manifest = DatabaseManager.get_manifest_tracks(mirror.id)
    assert len(manifest) == 1
    assert manifest[0].yt_video_id == "vid_imp1"

def test_quick_sync_safety_avoids_unmirrored_playlists():
    """UX Invariant 6: Quick sync must not blindly bulk-transfer unmirrored playlists."""
    init_db()
    # 1 active mirror exists for Alpha
    DatabaseManager.create_mirror(
        name="Playlist Alpha",
        destination="youtube",
        yt_playlist_id="PL_ALPHA",
        yt_playlist_name="Playlist Alpha",
        spotify_id="sp_alpha",
        spotify_track_count=10,
        yt_track_count=10
    )

    pl_alpha = Playlist(id="sp_alpha", name="Playlist Alpha", track_count=10)
    pl_beta = Playlist(id="sp_beta", name="Unmirrored Beta", track_count=50)

    from src.cli.main import InteractiveCLI
    cli = InteractiveCLI()
    mock_source = MagicMock()
    mock_source.get_playlists.return_value = [pl_alpha, pl_beta]
    cli.export_source = mock_source

    with patch.object(cli, "_ensure_destination_auth", return_value=True):
        with patch("InquirerPy.inquirer.select") as mock_select:
            # User chooses recommended "sync_mirrors"
            mock_select.return_value.execute.return_value = "sync_mirrors"
            with patch.object(cli, "_process_playlist", return_value={"name": "Playlist Alpha", "status": "IN_SYNC", "synced_tracks": 0, "total_tracks": 10}) as mock_proc:
                with patch.object(cli, "_post_sync_action_menu"):
                    cli.quick_sync_all_flow()

                    # Only Playlist Alpha should have been processed
                    assert mock_proc.call_count == 1
                    called_pl = mock_proc.call_args[0][0]
                    assert called_pl.name == "Playlist Alpha"

