import pytest
from unittest.mock import patch, MagicMock
from src.cli.tui import TUI
from src.core.models import Playlist, Track, MirroredPlaylist, MirrorStatus, SyncPlan, SyncPlanItem, PlanAction

def test_tui_render_mirrors_table(capsys):
    mirrors = [
        MirroredPlaylist(
            id="m1",
            name="Synthwave",
            yt_playlist_id="PL123",
            yt_playlist_name="Synthwave YTM",
            spotify_track_count=50,
            last_sync_status=MirrorStatus.IN_SYNC
        )
    ]
    TUI.render_mirrors_table(mirrors)
    captured = capsys.readouterr()
    assert "Synthwave" in captured.out
    assert "PL123" in captured.out
    assert "IN SYNC" in captured.out

def test_tui_render_diff_card_in_sync(capsys):
    pl = Playlist(name="Rock", track_count=10)
    plan = SyncPlan(
        id="p1",
        playlist_name="Rock",
        spotify_id="s1",
        source_track_count=10,
        destination_track_count=10,
        items=[
            SyncPlanItem(
                action=PlanAction.NO_OP,
                source_track=Track(name="Song 1", artist="Artist 1"),
                rationale="In sync"
            )
        ]
    )
    TUI.render_diff_card(pl, plan)
    captured = capsys.readouterr()
    assert "Rock" in captured.out
    assert "100% In Sync" in captured.out

def test_tui_render_diff_card_with_additions(capsys):
    pl = Playlist(name="Jazz", track_count=5)
    plan = SyncPlan(
        id="p2",
        playlist_name="Jazz",
        spotify_id="s2",
        source_track_count=5,
        destination_track_count=3,
        additions_count=2,
        items=[
            SyncPlanItem(
                action=PlanAction.ADD_TRACK,
                source_track=Track(name="Autumn Leaves", artist="Miles Davis"),
                rationale="New track"
            )
        ]
    )
    TUI.render_diff_card(pl, plan)
    captured = capsys.readouterr()
    assert "Autumn Leaves" in captured.out
    assert "Miles Davis" in captured.out
