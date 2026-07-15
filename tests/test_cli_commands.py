import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.cli.main import main, InteractiveCLI
from src.core.models import Playlist, Track, MirroredPlaylist, MirrorStatus
from src.core.database import DatabaseManager

def test_cli_list_command(capsys, tmp_path):
    sample_file = tmp_path / "test.json"
    sample_file.write_text('{"playlists": [{"name": "Synthwave", "tracks": [{"track": "Resonance", "artist": "HOME"}]}]}', encoding="utf-8")
    
    with patch("sys.argv", ["cli.py", "list", "--file", str(sample_file)]):
        main()

    captured = capsys.readouterr()
    assert "Synthwave" in captured.out
    assert "Playlists in test.json" in captured.out

def test_cli_auth_command(capsys):
    with patch("src.providers.youtube.ytmusic_dest.YouTubeMusicDestination.test_connection", return_value={"connected": True, "message": "Authenticated"}):
        with patch("sys.argv", ["cli.py", "auth"]):
            main()

    captured = capsys.readouterr()
    assert "CONNECTED (Active)" in captured.out
    assert "Authenticated" in captured.out

def test_cli_mirrors_command(capsys):
    with patch("src.core.database.DatabaseManager.list_mirrors", return_value=[
        MirroredPlaylist(id="m1", name="Chill Vibes", yt_playlist_id="yt1", yt_playlist_name="Chill Vibes", last_sync_status=MirrorStatus.IN_SYNC)
    ]):
        with patch("sys.argv", ["cli.py", "mirrors"]):
            main()

    captured = capsys.readouterr()
    assert "Active Mirrored Playlists" in captured.out
    assert "Chill Vibes" in captured.out

def test_cli_diff_command(capsys, tmp_path):
    sample_file = tmp_path / "test.json"
    sample_file.write_text('{"playlists": [{"name": "Synthwave", "tracks": [{"track": "Resonance", "artist": "HOME"}]}]}', encoding="utf-8")

    with patch("src.providers.youtube.ytmusic_dest.YouTubeMusicDestination.test_connection", return_value={"connected": True, "message": "OK"}):
        with patch("src.providers.youtube.ytmusic_dest.YouTubeMusicDestination.is_available", return_value=True):
            with patch("src.providers.youtube.ytmusic_dest.YouTubeMusicDestination.get_library_playlists", return_value=[]):
                with patch("builtins.input", return_value="m"):
                    with patch("sys.argv", ["cli.py", "diff", "--file", str(sample_file)]):
                        main()

    captured = capsys.readouterr()
    assert "Synthwave" in captured.out
    assert "not yet mirrored" in captured.out


