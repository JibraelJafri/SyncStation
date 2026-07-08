import pytest
from unittest.mock import MagicMock
from src.core.models import (
    Playlist,
    Track,
    CandidateTrack,
    SyncMode,
    JobStatus,
    ConfidenceTier,
    MirrorStatus
)
from src.core.database import init_db, DatabaseManager
from src.services.sync_service import SyncService
from src.providers.base import MusicSource, MusicDestination

class DummySource(MusicSource):
    def __init__(self, tracks):
        self._tracks = tracks

    def get_playlists(self):
        return [Playlist(id="dummy", name="Dummy Playlist", track_count=len(self._tracks), tracks=self._tracks)]

    def get_playlist_tracks(self, identifier):
        return Playlist(id="dummy", name="Dummy Playlist", track_count=len(self._tracks), tracks=self._tracks)

    def get_liked_tracks(self):
        return self._tracks

class MockYTDestination(MusicDestination):
    def __init__(self, existing_vids=None):
        self.created_playlists = {}
        self.playlist_items = {}
        self.existing_vids = existing_vids or []

    def is_available(self):
        return True

    def search_candidates(self, track: Track, limit: int = 5):
        vid = f"vid_{track.name.replace(' ', '_').lower()}"
        return [CandidateTrack(
            video_id=vid,
            title=track.name,
            artist=track.artist,
            duration_seconds=track.duration_seconds,
            result_type="song"
        )]

    def get_playlist(self, playlist_id: str):
        tracks = [
            Track(id=v, name=f"Song {v}", artist="Artist")
            for v in self.playlist_items.get(playlist_id, self.existing_vids)
        ]
        return Playlist(id=playlist_id, name="Test Dest", tracks=tracks)

    def create_playlist(self, name: str, description: str = "", privacy: str = "PRIVATE", video_ids=None):
        pl_id = f"yt_pl_{name.replace(' ', '_').lower()}"
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

def test_transfer_workflow():
    init_db()
    source_tracks = [
        Track(id="s1", uri="spotify:track:s1", name="Alpha Song", artist="Alpha Artist", duration_seconds=180.0),
        Track(id="s2", uri="spotify:track:s2", name="Beta Song", artist="Beta Artist", duration_seconds=200.0)
    ]
    source = DummySource(source_tracks)
    dest = MockYTDestination()
    sync_svc = SyncService()

    # 1. Analyze
    job = sync_svc.analyze_for_transfer(source, "Dummy Playlist", destination=dest)
    assert job.status == JobStatus.AWAITING_REVIEW
    assert job.matched_tracks == 2

    # 2. Execute Transfer
    report = sync_svc.execute_transfer(job.id, destination=dest)
    assert report.status == "COMPLETED"
    assert report.total_synced_tracks == 2
    assert report.mirror_id is not None

    # Verify mirror was established in DB
    mirror = DatabaseManager.get_mirror(report.mirror_id)
    assert mirror is not None
    assert mirror.name == "Dummy Playlist"
    assert mirror.last_sync_status == MirrorStatus.IN_SYNC

    # Verify manifest was populated
    manifest = DatabaseManager.get_manifest_tracks(mirror.id)
    assert len(manifest) == 2

def test_delta_sync_idempotency_no_duplicate_tracks():
    init_db()
    source_tracks = [
        Track(id="s1", uri="spotify:track:s1", name="Alpha Song", artist="Alpha Artist", duration_seconds=180.0),
        Track(id="s2", uri="spotify:track:s2", name="Beta Song", artist="Beta Artist", duration_seconds=200.0)
    ]
    source = DummySource(source_tracks)
    dest = MockYTDestination()
    sync_svc = SyncService()

    # 1. Initial transfer
    job = sync_svc.analyze_for_transfer(source, "Dummy Playlist", destination=dest)
    transfer_report = sync_svc.execute_transfer(job.id, destination=dest)
    mirror_id = transfer_report.mirror_id

    # 2. Run sync immediately with NO changes
    delta_report = sync_svc.execute_delta_sync(mirror_id, destination=dest, source=source)
    assert delta_report.total_synced_tracks == 0
    assert delta_report.already_synchronized_tracks == 2

    # 3. Add 1 new track to source
    updated_tracks = source_tracks + [
        Track(id="s3", uri="spotify:track:s3", name="Gamma Song", artist="Gamma Artist", duration_seconds=220.0)
    ]
    updated_source = DummySource(updated_tracks)

    # 4. Generate plan and delta sync
    plan = sync_svc.generate_sync_plan(mirror_id, destination=dest, source=updated_source)
    # The delta plan should detect exactly 1 addition
    delta_report2 = sync_svc.execute_delta_sync(mirror_id, destination=dest, source=updated_source)
    # Total tracks added should only be the new song
    assert delta_report2.total_synced_tracks == 1
    assert delta_report2.already_synchronized_tracks == 2

    # Manifest should now have 3 tracks
    manifest = DatabaseManager.get_manifest_tracks(mirror_id)
    assert len(manifest) == 3
