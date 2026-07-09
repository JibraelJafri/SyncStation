import pytest
from src.core.models import (
    Playlist,
    Track,
    MirrorTrackManifest,
    MirroredPlaylist,
    PlanAction,
    SyncPolicy,
    AdditionPolicy,
    RemovalPolicy,
    AmbiguousMatchPolicy
)
from src.domain.sync_planner import SyncPlanner

def test_planner_additions_only():
    source = Playlist(
        name="Workout",
        tracks=[
            Track(id="1", uri="spotify:track:1", name="Track 1", artist="Artist A"),
            Track(id="2", uri="spotify:track:2", name="Track 2", artist="Artist B"),
            Track(id="3", uri="spotify:track:3", name="Track 3", artist="Artist C"),
        ]
    )
    # Existing destination only has Track 1
    dest_tracks = [Track(id="yt_vid_1", name="Track 1", artist="Artist A")]
    manifest = [
        MirrorTrackManifest(
            mirror_id="m1",
            spotify_uri="spotify:track:1",
            spotify_name="Track 1",
            spotify_artist="Artist A",
            yt_video_id="yt_vid_1",
            yt_title="Track 1",
            yt_artist="Artist A"
        )
    ]

    plan = SyncPlanner.generate_plan(source, dest_tracks, manifest_tracks=manifest)
    assert plan.additions_count == 2
    assert plan.removals_count == 0
    assert plan.unchanged_count == 1

    actions = [it.action for it in plan.items]
    assert actions == [PlanAction.NO_OP, PlanAction.ADD_TRACK, PlanAction.ADD_TRACK]

def test_planner_removals_policy_never_remove():
    source = Playlist(
        name="Road Trip",
        tracks=[
            Track(id="1", uri="spotify:track:1", name="Track 1", artist="Artist A")
        ]
    )
    dest_tracks = [
        Track(id="yt_1", name="Track 1", artist="Artist A"),
        Track(id="yt_2", name="Track 2", artist="Artist B")
    ]
    manifest = [
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:1", spotify_name="Track 1", spotify_artist="Artist A", yt_video_id="yt_1", yt_title="Track 1", yt_artist="Artist A"),
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:2", spotify_name="Track 2", spotify_artist="Artist B", yt_video_id="yt_2", yt_title="Track 2", yt_artist="Artist B")
    ]

    policy = SyncPolicy(removals=RemovalPolicy.NEVER_REMOVE)
    plan = SyncPlanner.generate_plan(source, dest_tracks, manifest_tracks=manifest, policy=policy)

    assert plan.additions_count == 0
    assert plan.removals_count == 0  # Marked as SKIP_TRACK
    skip_items = [it for it in plan.items if it.action == PlanAction.SKIP_TRACK]
    assert len(skip_items) == 1
    assert skip_items[0].destination_video_id == "yt_2"

def test_planner_removals_policy_mirror_removals():
    source = Playlist(
        name="Road Trip",
        tracks=[
            Track(id="1", uri="spotify:track:1", name="Track 1", artist="Artist A")
        ]
    )
    dest_tracks = [
        Track(id="yt_1", name="Track 1", artist="Artist A"),
        Track(id="yt_2", name="Track 2", artist="Artist B")
    ]
    manifest = [
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:1", spotify_name="Track 1", spotify_artist="Artist A", yt_video_id="yt_1", yt_title="Track 1", yt_artist="Artist A"),
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:2", spotify_name="Track 2", spotify_artist="Artist B", yt_video_id="yt_2", yt_title="Track 2", yt_artist="Artist B")
    ]

    policy = SyncPolicy(removals=RemovalPolicy.MIRROR_REMOVALS)
    plan = SyncPlanner.generate_plan(source, dest_tracks, manifest_tracks=manifest, policy=policy)

    assert plan.removals_count == 1
    remove_items = [it for it in plan.items if it.action == PlanAction.REMOVE_TRACK]
    assert len(remove_items) == 1
    assert remove_items[0].is_accepted is True

def test_planner_accidental_deletion_restoration():
    """Scenario: User accidentally deleted Track 2 from YouTube Music playlist."""
    source = Playlist(
        name="Favorites",
        tracks=[
            Track(id="1", uri="spotify:track:1", name="Track 1", artist="Artist A"),
            Track(id="2", uri="spotify:track:2", name="Track 2", artist="Artist B"),
        ]
    )
    # Live YT playlist ONLY has Track 1 (Track 2 was deleted from YT Music)
    dest_tracks = [
        Track(id="yt_1", name="Track 1", artist="Artist A")
    ]
    # But manifest knows we previously synced Track 2 to yt_2
    manifest = [
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:1", spotify_name="Track 1", spotify_artist="Artist A", yt_video_id="yt_1", yt_title="Track 1", yt_artist="Artist A"),
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:2", spotify_name="Track 2", spotify_artist="Artist B", yt_video_id="yt_2", yt_title="Track 2", yt_artist="Artist B")
    ]

    plan = SyncPlanner.generate_plan(source, dest_tracks, manifest_tracks=manifest)
    assert plan.unchanged_count == 1
    assert plan.restorations_count == 1
    assert plan.additions_count == 1
    
    restore_item = next(it for it in plan.items if it.action == PlanAction.ADD_TRACK)
    assert restore_item.destination_video_id == "yt_2"
    assert "deleted on YT" in restore_item.rationale

def test_planner_extra_youtube_only_tracks():
    """Scenario: User added Track 3 directly to YouTube Music."""
    source = Playlist(
        name="Chill",
        tracks=[
            Track(id="1", uri="spotify:track:1", name="Track 1", artist="Artist A")
        ]
    )
    dest_tracks = [
        Track(id="yt_1", name="Track 1", artist="Artist A"),
        Track(id="yt_extra", name="Bonus Beat", artist="Unknown DJ")
    ]
    manifest = [
        MirrorTrackManifest(mirror_id="m1", spotify_uri="spotify:track:1", spotify_name="Track 1", spotify_artist="Artist A", yt_video_id="yt_1", yt_title="Track 1", yt_artist="Artist A")
    ]

    plan = SyncPlanner.generate_plan(source, dest_tracks, manifest_tracks=manifest)
    assert plan.extra_dest_count == 1
    extra_item = next(it for it in plan.items if it.destination_video_id == "yt_extra")
    assert extra_item.action == PlanAction.SKIP_TRACK
    assert "YouTube Music-only" in extra_item.rationale

