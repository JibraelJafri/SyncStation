import pytest
import json
import csv
from pathlib import Path
from src.core.models import Playlist, Track
from src.providers.spotify.export_source import SpotifyExportSource

def test_parse_spicetify_json_format(tmp_path):
    sample_data = {
        "playlists": [
            {
                "name": "Synthwave 80s",
                "uri": "spotify:playlist:synth123",
                "items": [
                    {
                        "track": {
                            "trackName": "Resonance",
                            "artistName": "Home",
                            "albumName": "Odyssey",
                            "trackUri": "spotify:track:home1"
                        }
                    },
                    {
                        "track": {
                            "trackName": "Tech Noir",
                            "artistName": "Gunship",
                            "albumName": "Gunship",
                            "trackUri": "spotify:track:gunship1"
                        }
                    }
                ]
            }
        ]
    }

    json_file = tmp_path / "export_test.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(sample_data, f)

    source = SpotifyExportSource(file_path=json_file)
    playlists = source.get_playlists()
    assert len(playlists) == 1
    assert playlists[0].name == "Synthwave 80s"
    assert playlists[0].track_count == 2

    tracks_pl = source.get_playlist_tracks("Synthwave 80s")
    assert len(tracks_pl.tracks) == 2
    assert tracks_pl.tracks[0].name == "Resonance"
    assert tracks_pl.tracks[0].artist == "Home"

def test_parse_csv_export_format(tmp_path):
    csv_file = tmp_path / "chill_vibes.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Track Name", "Artist Name(s)", "Album Name", "Spotify ID"])
        writer.writerow(["Sunset Lover", "Petit Biscuit", "Presence", "spotify:track:pb1"])
        writer.writerow(["Intro", "The xx", "xx", "spotify:track:xx1"])

    source = SpotifyExportSource(file_path=csv_file)
    playlists = source.get_playlists()
    assert len(playlists) == 1
    assert playlists[0].name == "chill_vibes"

    pl = source.get_playlist_tracks("chill_vibes")
    assert len(pl.tracks) == 2
    assert pl.tracks[0].name == "Sunset Lover"
    assert pl.tracks[1].artist == "The xx"

def test_parse_delisted_tracks_missing_fields(tmp_path):
    sample_data = {
        "playlists": [
            {
                "name": "Old Rarities",
                "items": [
                    {
                        "track": {
                            # Missing trackName, has albumName
                            "trackName": "",
                            "artistName": "Rare Artist",
                            "albumName": "Unknown Gem",
                            "trackUri": "spotify:track:rare1"
                        }
                    },
                    {
                        "track": {
                            # artistName is a list of dicts
                            "trackName": "Collab Song",
                            "artistName": [{"name": "Artist 1"}, {"name": "Artist 2"}],
                            "albumName": "Collab Album",
                            "trackUri": "spotify:track:collab1"
                        }
                    }
                ]
            }
        ]
    }

    json_file = tmp_path / "delisted.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(sample_data, f)

    source = SpotifyExportSource(file_path=json_file)
    pl = source.get_playlist_tracks("Old Rarities")
    assert len(pl.tracks) == 2
    assert pl.tracks[0].name == "Unknown Gem"
    assert "Artist 1" in pl.tracks[1].artist
    assert "Artist 2" in pl.tracks[1].artist
