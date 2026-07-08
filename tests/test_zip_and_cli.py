import io
import json
import zipfile
import tempfile
from pathlib import Path
import pytest

from src.providers.spotify.export_source import SpotifyExportSource
from src.core.models import Playlist, Track
from src.domain.normalizer import strip_edition_noise, extract_track_flags, extract_title_and_featured

def test_spotify_zip_export_parsing():
    """Verify that SpotifyExportSource properly extracts and parses zip files containing JSON playlists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "spotify_data.zip"
        
        pl1_data = {
            "playlists": [
                {
                    "name": "Synthwave Chill",
                    "items": [
                        {
                            "track": {
                                "trackName": "Resonance",
                                "artistName": "HOME",
                                "albumName": "Odyssey",
                                "trackUri": "spotify:track:65r94rVdiMwqXyQFEr3tqT",
                                "durationMs": 212000
                            }
                        }
                    ]
                }
            ]
        }
        
        pl2_data = {
            "name": "Rock Classics",
            "items": [
                {
                    "track": {
                        "trackName": "Bohemian Rhapsody",
                        "artistName": "Queen",
                        "albumName": "A Night at the Opera",
                        "trackUri": "spotify:track:4u7EnebtmKWzUH433cf5Qv",
                        "durationMs": 354000
                    }
                }
            ]
        }

        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("Playlist1.json", json.dumps(pl1_data))
            z.writestr("Playlist2.json", json.dumps(pl2_data))

        source = SpotifyExportSource(file_path=zip_path)
        playlists = source.get_playlists()
        assert len(playlists) == 2
        names = {p.name for p in playlists}
        assert "Synthwave Chill" in names
        assert "Rock Classics" in names

        pl1 = source.get_playlist_tracks("Synthwave Chill")
        assert len(pl1.tracks) == 1
        assert pl1.tracks[0].name == "Resonance"
        assert pl1.tracks[0].artist == "HOME"
        assert abs(pl1.tracks[0].duration_seconds - 212.0) < 0.1

def test_enhanced_edition_noise_stripping():
    """Verify new edition and video noise patterns are accurately stripped."""
    assert strip_edition_noise("Song Title (Official Music Video)") == "Song Title"
    assert strip_edition_noise("Song Title [Official Audio]") == "Song Title"
    assert strip_edition_noise("Song Title (Lyric Video)") == "Song Title"
    assert strip_edition_noise("Song Title (Visualizer)") == "Song Title"
    assert strip_edition_noise("Song Title [HD]") == "Song Title"
    assert strip_edition_noise("Song Title (4K Remaster)") == "Song Title"
    assert strip_edition_noise("Song Title - Super Deluxe Edition") == "Song Title"
    assert strip_edition_noise("Song Title (Bonus Track Version)") == "Song Title"
    assert strip_edition_noise("Song Title - Music From The Motion Picture Soundtrack") == "Song Title"

def test_featured_and_version_flags():
    title, feat = extract_title_and_featured("Starboy (feat. Daft Punk)")
    assert title == "Starboy"
    assert "daft punk" in [f.lower() for f in feat]

    flags = extract_track_flags("Hotel California (Live at The Forum, 1976)", "Eagles")
    assert flags["is_live"] is True
    assert flags["is_remix"] is False

    flags_remix = extract_track_flags("Levitating (The Blessed Madonna Remix)", "Dua Lipa")
    assert flags_remix["is_remix"] is True
    assert flags_remix["is_live"] is False
