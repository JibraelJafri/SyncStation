import uuid
from typing import List, Dict, Any, Optional, Set
from src.core.models import (
    Playlist,
    Track,
    CandidateTrack,
    MirroredPlaylist,
    MirrorTrackManifest,
    SyncPlan,
    SyncPlanItem,
    PlanAction,
    SyncPolicy,
    AdditionPolicy,
    RemovalPolicy,
    AmbiguousMatchPolicy,
    ConfidenceTier
)
from src.domain.normalizer import clean_query_string

class SyncPlanner:
    @staticmethod
    def generate_plan(
        source_playlist: Playlist,
        dest_tracks: List[Track],
        manifest_tracks: Optional[List[MirrorTrackManifest]] = None,
        mirror: Optional[MirroredPlaylist] = None,
        policy: Optional[SyncPolicy] = None
    ) -> SyncPlan:
        plan_id = str(uuid.uuid4())[:8]
        active_policy = policy or SyncPolicy(
            additions=mirror.sync_policy_additions if mirror else AdditionPolicy.AUTO_ADD,
            removals=mirror.sync_policy_removals if mirror else RemovalPolicy.ASK_BEFORE_REMOVE,
            ambiguous=mirror.sync_policy_matching if mirror else AmbiguousMatchPolicy.ASK_REVIEW
        )

        plan_items: List[SyncPlanItem] = []
        source_tracks = source_playlist.tracks

        from rapidfuzz import fuzz

        # Existing destination tracks by video_id and normalized query
        dest_vids: Set[str] = {t.id for t in dest_tracks if t.id}
        dest_queries: Dict[str, str] = {}
        for t in dest_tracks:
            if not t.name:
                continue
            # 1. Standard "artist track"
            dest_queries[clean_query_string(t.artist, t.name).lower()] = t.id
            # 2. Reverse "track artist"
            dest_queries[clean_query_string(t.name, t.artist).lower()] = t.id
            # 3. Title alone
            t_only = clean_query_string("", t.name).lower()
            if t_only:
                dest_queries[t_only] = t.id
        
        # Build manifest lookups
        manifest_by_sp_uri: Dict[str, MirrorTrackManifest] = {}
        manifest_by_vid: Dict[str, MirrorTrackManifest] = {}
        if manifest_tracks:
            for m in manifest_tracks:
                if m.spotify_uri:
                    manifest_by_sp_uri[m.spotify_uri] = m
                if m.yt_video_id:
                    manifest_by_vid[m.yt_video_id] = m

        dest_display = "Deezer" if (mirror and mirror.destination == "deezer") else "YouTube Music"

        # Identify duplicates on destination playlist
        seen_dest_track_ids: Set[str] = set()
        duplicate_dest_tracks: List[Track] = []
        for t in dest_tracks:
            if not t.id:
                continue
            if t.id in seen_dest_track_ids:
                duplicate_dest_tracks.append(t)
            else:
                seen_dest_track_ids.add(t.id)

        # 1. Process Source Tracks (Additions vs Already Present vs Restorations)
        source_uris_in_plan: Set[str] = set()
        matched_dest_vids: Set[str] = set()

        for track in source_tracks:
            sp_uri = track.uri or (f"spotify:track:{track.id}" if track.id else f"query:{clean_query_string(track.artist, track.name)}")
            source_uris_in_plan.add(sp_uri)
            q_clean = clean_query_string(track.artist, track.name).lower()
            t_clean = clean_query_string("", track.name).lower()

            # Check manifest entry
            manifest_entry = manifest_by_sp_uri.get(sp_uri)
            matched_vid = None

            if manifest_entry and (not dest_vids or manifest_entry.yt_video_id in dest_vids):
                matched_vid = manifest_entry.yt_video_id
            elif q_clean in dest_queries:
                matched_vid = dest_queries[q_clean]
            elif t_clean in dest_queries:
                matched_vid = dest_queries[t_clean]
            elif dest_tracks:
                # Fuzzy token match for slight naming variances on destination
                comb_sp = f"{track.artist} {track.name}".lower()
                for dt in dest_tracks:
                    if not dt.name or dt.id in matched_dest_vids:
                        continue
                    comb_yt = f"{dt.artist} {dt.name}".lower()
                    if fuzz.token_set_ratio(comb_sp, comb_yt) >= 90:
                        matched_vid = dt.id
                        break

            if matched_vid:
                matched_dest_vids.add(matched_vid)
                plan_items.append(SyncPlanItem(
                    action=PlanAction.NO_OP,
                    source_track=track,
                    destination_video_id=matched_vid,
                    confidence_tier=ConfidenceTier.EXACT,
                    confidence_score=1.0,
                    rationale=f"Already present on {dest_display} (in sync)",
                    is_accepted=True
                ))
            elif manifest_entry and dest_vids and manifest_entry.yt_video_id not in dest_vids:
                dest_short = "Deezer" if (mirror and mirror.destination == "deezer") else "YT"
                plan_items.append(SyncPlanItem(
                    action=PlanAction.ADD_TRACK,
                    source_track=track,
                    destination_video_id=manifest_entry.yt_video_id,
                    confidence_tier=ConfidenceTier.EXACT,
                    confidence_score=1.0,
                    rationale=f"Missing on {dest_display} (deleted on {dest_short} — will restore: '{track.artist} - {track.name}')",
                    requires_user_review=False,
                    is_accepted=True
                ))
            else:
                # New track from Spotify that needs to be matched and added
                plan_items.append(SyncPlanItem(
                    action=PlanAction.ADD_TRACK,
                    source_track=track,
                    confidence_tier=ConfidenceTier.NO_MATCH,
                    rationale="New track to be matched and transferred",
                    requires_user_review=(active_policy.additions == AdditionPolicy.ASK_REVIEW),
                    is_accepted=True
                ))

        # 2. Process Destination Removals (Spotify Deletions)
        # Tracks present in destination manifest that are no longer in source
        if manifest_tracks:
            for m in manifest_tracks:
                if m.spotify_uri not in source_uris_in_plan:
                    if dest_vids and m.yt_video_id not in dest_vids:
                        continue
                    if active_policy.removals == RemovalPolicy.NEVER_REMOVE:
                        plan_items.append(SyncPlanItem(
                            action=PlanAction.SKIP_TRACK,
                            destination_video_id=m.yt_video_id,
                            rationale=f"Removed from Spotify: '{m.spotify_artist} - {m.spotify_name}' (kept per 'Never Remove' policy)",
                            is_accepted=False
                        ))
                    elif active_policy.removals == RemovalPolicy.ASK_BEFORE_REMOVE:
                        plan_items.append(SyncPlanItem(
                            action=PlanAction.REMOVE_TRACK,
                            destination_video_id=m.yt_video_id,
                            rationale=f"Removed from Spotify: '{m.spotify_artist} - {m.spotify_name}'",
                            requires_user_review=True,
                            is_accepted=False # Requires user review / opt-in
                        ))
                    elif active_policy.removals == RemovalPolicy.MIRROR_REMOVALS:
                        plan_items.append(SyncPlanItem(
                            action=PlanAction.REMOVE_TRACK,
                            destination_video_id=m.yt_video_id,
                            rationale=f"Mirroring Spotify removal: '{m.spotify_artist} - {m.spotify_name}'",
                            requires_user_review=False,
                            is_accepted=True
                        ))

        # 3. Process Duplicate Tracks on Destination
        for dt in duplicate_dest_tracks:
            plan_items.append(SyncPlanItem(
                action=PlanAction.REMOVE_TRACK,
                destination_video_id=dt.id,
                source_track=dt,
                confidence_tier=ConfidenceTier.EXACT,
                confidence_score=1.0,
                rationale=f"Duplicate on {dest_display}: '{dt.artist} - {dt.name}'",
                requires_user_review=(active_policy.removals == RemovalPolicy.ASK_BEFORE_REMOVE),
                is_accepted=(active_policy.removals == RemovalPolicy.MIRROR_REMOVALS)
            ))

        # 4. Process Extra Destination-only Tracks (Tracks added directly on destination)
        if dest_tracks:
            for t in dest_tracks:
                if t.id and t.id not in matched_dest_vids and t.id not in manifest_by_vid and t not in duplicate_dest_tracks:
                    if active_policy.removals == RemovalPolicy.NEVER_REMOVE:
                        plan_items.append(SyncPlanItem(
                            action=PlanAction.SKIP_TRACK,
                            destination_video_id=t.id,
                            source_track=t,
                            rationale=f"{dest_display}-only track: '{t.artist} - {t.name}' (kept per 'Never Remove' policy)",
                            requires_user_review=False,
                            is_accepted=False
                        ))
                    elif active_policy.removals == RemovalPolicy.ASK_BEFORE_REMOVE:
                        plan_items.append(SyncPlanItem(
                            action=PlanAction.REMOVE_TRACK,
                            destination_video_id=t.id,
                            source_track=t,
                            rationale=f"{dest_display}-only track (not in Spotify): '{t.artist} - {t.name}'",
                            requires_user_review=True,
                            is_accepted=False
                        ))
                    elif active_policy.removals == RemovalPolicy.MIRROR_REMOVALS:
                        plan_items.append(SyncPlanItem(
                            action=PlanAction.REMOVE_TRACK,
                            destination_video_id=t.id,
                            source_track=t,
                            rationale=f"Mirroring Spotify removal ({dest_display}-only): '{t.artist} - {t.name}'",
                            requires_user_review=False,
                            is_accepted=True
                        ))

        # Calculate summary statistics
        additions_count = sum(1 for item in plan_items if item.action == PlanAction.ADD_TRACK and not item.destination_video_id)
        restorations_count = sum(1 for item in plan_items if item.action == PlanAction.ADD_TRACK and item.destination_video_id)
        extra_dest_count = sum(1 for item in plan_items if item.action == PlanAction.SKIP_TRACK and "-only track" in item.rationale)
        removals_count = sum(1 for item in plan_items if item.action == PlanAction.REMOVE_TRACK)
        unchanged_count = sum(1 for item in plan_items if item.action == PlanAction.NO_OP)

        rename_detected = False
        new_name = None
        if mirror and mirror.name and source_playlist.name and mirror.name != source_playlist.name:
            rename_detected = True
            new_name = source_playlist.name

        return SyncPlan(
            id=plan_id,
            mirror_id=mirror.id if mirror else None,
            playlist_name=source_playlist.name,
            spotify_id=source_playlist.id or (mirror.spotify_id if mirror else ""),
            yt_playlist_id=mirror.yt_playlist_id if mirror else None,
            source_track_count=len(source_tracks),
            destination_track_count=len(dest_tracks),
            additions_count=additions_count + restorations_count,
            restorations_count=restorations_count,
            extra_dest_count=extra_dest_count,
            removals_count=removals_count,
            unchanged_count=unchanged_count,
            rename_detected=rename_detected,
            new_name=new_name,
            items=plan_items
        )
