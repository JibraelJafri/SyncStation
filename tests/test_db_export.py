import pytest
from pathlib import Path
from src.core.config import PROJECT_ROOT
from src.providers.spotify.export_source import SpotifyExportSource
from src.core.database import DatabaseManager, init_db, get_db
from src.domain.overrides import OverrideManager
from src.core.models import Track, CandidateTrack, ConfidenceTier

def test_export_source_parsing(tmp_path):
    sample_data = {
        "playlists": [
            {
                "name": "Manifest Destiny",
                "uri": "spotify:playlist:manifest123",
                "items": [
                    {
                        "track": {
                            "trackName": "Track 1",
                            "artistName": "Artist 1",
                            "albumName": "Album 1",
                            "trackUri": "spotify:track:t1"
                        }
                    }
                ]
            }
        ]
    }
    json_file = tmp_path / "spotify-export-sample.json"
    import json
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(sample_data, f)

    source = SpotifyExportSource(file_path=json_file)
    playlists = source.get_playlists()
    assert len(playlists) > 0

    manifest = next((p for p in playlists if p.name == "Manifest Destiny"), None)
    assert manifest is not None
    assert manifest.track_count == 1

    tracks_pl = source.get_playlist_tracks("Manifest Destiny")
    assert len(tracks_pl.tracks) == 1
    assert tracks_pl.tracks[0].name == "Track 1"

def test_database_and_overrides(tmp_path):
    init_db()

    # Save cached match
    DatabaseManager.save_cached_match(
        query="test artist test song",
        video_id="vid_123",
        title="Test Song",
        artist="Test Artist",
        duration=180.0,
        score=0.98
    )

    cached = DatabaseManager.get_cached_match("test artist test song")
    assert cached is not None
    assert cached["video_id"] == "vid_123"

    # User override
    t = Track(name="Special Song", artist="Indie Artist", uri="spotify:track:xyz123")
    c = CandidateTrack(video_id="override_vid", title="Special Song (Official)", artist="Indie Artist")

    OverrideManager.set_override(t, c)
    match = OverrideManager.get_override_match(t)
    assert match is not None
    assert match.matched_track.video_id == "override_vid"
    assert match.confidence_tier == ConfidenceTier.EXACT
    assert match.is_manual_override is True
