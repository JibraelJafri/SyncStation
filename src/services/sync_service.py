import uuid
import time
import asyncio
from typing import List, Dict, Any, Optional, Set, Callable
from datetime import datetime

from src.core.models import (
    Track,
    Playlist,
    MatchResult,
    ConfidenceTier,
    SyncJob,
    SyncMode,
    JobStatus,
    SyncReport,
    CandidateTrack,
    MirroredPlaylist,
    MirrorTrackManifest,
    MirrorStatus,
    SyncPlan,
    SyncPlanItem,
    PlanAction,
    SyncPolicy,
    AdditionPolicy,
    RemovalPolicy,
    AmbiguousMatchPolicy
)
from src.core.database import DatabaseManager, get_db
from src.core.logger import logger
from src.domain.normalizer import clean_query_string
from src.domain.matcher import MatchingEngine
from src.domain.overrides import OverrideManager
from src.domain.sync_planner import SyncPlanner
from src.providers.base import MusicSource, MusicDestination
from src.providers.spotify.unified_provider import UnifiedSpotifyProvider
from src.providers.spotify.web_source import SpotifyWebSource
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination

class SyncService:
    def __init__(self):
        self.active_jobs: Dict[str, SyncJob] = {}
        self.cancel_flags: Dict[str, bool] = {}
        self.pause_flags: Dict[str, bool] = {}
        self.active_plans: Dict[str, SyncPlan] = {}

    def get_job(self, job_id: str) -> Optional[SyncJob]:
        if job_id in self.active_jobs:
            return self.active_jobs[job_id]
        db_job = DatabaseManager.get_job(job_id)
        if db_job:
            return SyncJob(**db_job)
        return None

    def cancel_job(self, job_id: str):
        self.cancel_flags[job_id] = True
        if job_id in self.active_jobs:
            self.active_jobs[job_id].status = JobStatus.CANCELLED
        DatabaseManager.update_job_status(job_id, JobStatus.CANCELLED.value)

    def pause_job(self, job_id: str):
        self.pause_flags[job_id] = True
        if job_id in self.active_jobs:
            self.active_jobs[job_id].status = JobStatus.PAUSED
        DatabaseManager.update_job_status(job_id, JobStatus.PAUSED.value)

    # ─── WORKFLOW 1: INITIAL TRANSFER (Establish Mirror) ─────────────────
    def analyze_for_transfer(
        self,
        source: MusicSource,
        playlist_identifier: str,
        destination: Optional[MusicDestination] = None,
        job_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> SyncJob:
        dest = destination or YouTubeMusicDestination()
        playlist = source.get_playlist_tracks(playlist_identifier)
        
        job_id = job_id or str(uuid.uuid4())[:8]
        job = SyncJob(
            id=job_id,
            playlist_name=playlist.name,
            spotify_url_or_id=playlist.uri or playlist_identifier,
            mode=SyncMode.TRANSFER,
            status=JobStatus.ANALYZING,
            total_tracks=len(playlist.tracks)
        )
        self.active_jobs[job_id] = job
        DatabaseManager.create_job(
            job.id, job.playlist_name, job.spotify_url_or_id,
            job.mode.value, job.total_tracks
        )

        start_time = time.time()

        for idx, track in enumerate(playlist.tracks, 1):
            if self.cancel_flags.get(job_id, False):
                job.status = JobStatus.CANCELLED
                DatabaseManager.update_job_status(job.id, JobStatus.CANCELLED.value)
                return job

            job.current_track_name = f"{track.artist} - {track.name}"
            query_key = clean_query_string(track.artist, track.name)

            # 1. User Override Check
            match_res = OverrideManager.get_override_match(track)

            # 2. Database Matches Cache Check
            if not match_res:
                cached = DatabaseManager.get_cached_match(query_key)
                if cached:
                    cand = CandidateTrack(
                        video_id=cached["video_id"],
                        title=cached["title"] or track.name,
                        artist=cached["artist"] or track.artist,
                        duration_seconds=cached["duration_seconds"],
                        result_type=cached["result_type"]
                    )
                    score = cached.get("confidence_score", 1.0)
                    tier = ConfidenceTier.EXACT if score >= 0.92 else (ConfidenceTier.HIGH if score >= 0.80 else ConfidenceTier.PROBABLE)
                    match_res = MatchResult(
                        source_track=track,
                        matched_track=cand,
                        confidence_score=score,
                        confidence_tier=tier,
                        rationale="Loaded from verified matches cache",
                        is_accepted=True
                    )

            # 3. Live YouTube Music Search
            if not match_res:
                candidates = dest.search_candidates(track, limit=5)
                match_res = MatchingEngine.match_track(track, candidates)

                if match_res.matched_track and match_res.confidence_tier in [ConfidenceTier.EXACT, ConfidenceTier.HIGH]:
                    cand = match_res.matched_track
                    DatabaseManager.save_cached_match(
                        query=query_key,
                        video_id=cand.video_id,
                        title=cand.title,
                        artist=cand.artist,
                        duration=cand.duration_seconds,
                        result_type=cand.result_type,
                        score=match_res.confidence_score,
                        isrc=track.isrc
                    )

            # Save sync item to DB
            with get_db() as conn:
                conn.execute("""
                INSERT INTO sync_items (
                    job_id, track_index, source_name, source_artist, source_album, source_duration,
                    source_uri, matched_video_id, matched_title, matched_artist, confidence_score,
                    confidence_tier, rationale, status, is_manual_override, is_accepted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job.id, idx, track.name, track.artist, track.album, track.duration_seconds,
                    track.uri or f"spotify:track:{track.id}",
                    match_res.matched_track.video_id if match_res.matched_track else None,
                    match_res.matched_track.title if match_res.matched_track else None,
                    match_res.matched_track.artist if match_res.matched_track else None,
                    match_res.confidence_score, match_res.confidence_tier.value, match_res.rationale,
                    "ANALYZED", int(match_res.is_manual_override), int(match_res.is_accepted)
                ))

            job.processed_tracks = idx
            if match_res.matched_track and match_res.is_accepted:
                job.matched_tracks += 1

            if progress_callback:
                try:
                    progress_callback(idx, job.total_tracks, job.current_track_name)
                except Exception:
                    pass

            if idx % 5 == 0 or idx == job.total_tracks:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                eta = int((job.total_tracks - idx) / rate) if rate > 0 else 0
                job.eta_seconds = eta
                
                DatabaseManager.update_job_progress(
                    job.id, idx, job.matched_tracks, job.current_track_name,
                    thumbnail_url=match_res.matched_track.thumbnail_url if match_res.matched_track else None,
                    eta_seconds=eta
                )

        job.status = JobStatus.AWAITING_REVIEW
        DatabaseManager.update_job_status(job.id, JobStatus.AWAITING_REVIEW.value)
        return job

    def execute_transfer(
        self,
        job_id: str,
        destination: Optional[MusicDestination] = None,
        privacy: str = "PRIVATE",
        auto_like: bool = False
    ) -> SyncReport:
        dest = destination or YouTubeMusicDestination()
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found.")

        job.status = JobStatus.SYNCING
        DatabaseManager.update_job_status(job.id, JobStatus.SYNCING.value)

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM sync_items
            WHERE job_id = ? AND is_accepted = 1
            ORDER BY track_index ASC
            """, (job_id,))
            rows = cur.fetchall()

        matched_items = [dict(r) for r in rows if r["matched_video_id"]]
        video_ids = [item["matched_video_id"] for item in matched_items]

        start_time = time.time()
        # 1. Create YouTube Music destination playlist
        initial = video_ids[:50]
        yt_pl_id = dest.create_playlist(
            name=job.playlist_name,
            description=f"Mirrored from Spotify ({len(matched_items)} tracks)",
            privacy=privacy,
            video_ids=initial
        )
        job.yt_playlist_id = yt_pl_id
        job.synced_tracks = len(initial)
        DatabaseManager.update_job_status(job.id, JobStatus.SYNCING.value, yt_playlist_id=yt_pl_id, synced_count=job.synced_tracks)

        # 2. Add remaining tracks in chunks
        if len(video_ids) > 50:
            remaining = video_ids[50:]
            added = dest.add_tracks_to_playlist(yt_pl_id, remaining)
            job.synced_tracks += added
            DatabaseManager.update_job_status(job.id, JobStatus.SYNCING.value, synced_count=job.synced_tracks)

        # 3. Optional auto-like
        if auto_like:
            for vid in video_ids:
                dest.rate_track(vid, "LIKE")

        # 4. Establish Persistent Mirrored Playlist
        mirror = DatabaseManager.create_mirror(
            name=job.playlist_name,
            yt_playlist_id=yt_pl_id,
            yt_playlist_name=job.playlist_name,
            spotify_id=job.spotify_url_or_id,
            spotify_uri=f"spotify:playlist:{job.spotify_url_or_id}",
            spotify_track_count=job.total_tracks,
            yt_track_count=job.synced_tracks
        )
        job.mirror_id = mirror.id

        # 5. Save Track Manifest
        manifest_entries = []
        for item in matched_items:
            manifest_entries.append({
                "spotify_uri": item["source_uri"],
                "spotify_name": item["source_name"],
                "spotify_artist": item["source_artist"],
                "spotify_album": item["source_album"],
                "spotify_duration": item["source_duration"],
                "yt_video_id": item["matched_video_id"],
                "yt_title": item["matched_title"],
                "yt_artist": item["matched_artist"]
            })
        DatabaseManager.save_manifest_tracks(mirror.id, manifest_entries)

        duration_sec = round(time.time() - start_time, 2)
        job.status = JobStatus.COMPLETED
        DatabaseManager.update_job_status(job.id, JobStatus.COMPLETED.value, synced_count=job.synced_tracks)

        exact = sum(1 for item in matched_items if item.get("confidence_tier") == "EXACT")
        high = sum(1 for item in matched_items if item.get("confidence_tier") == "HIGH")
        probable = sum(1 for item in matched_items if item.get("confidence_tier") == "PROBABLE")
        ambiguous = sum(1 for item in matched_items if item.get("confidence_tier") == "AMBIGUOUS")
        unmatched = job.total_tracks - len(matched_items)

        yt_url = f"https://music.youtube.com/playlist?list={yt_pl_id}"
        report = SyncReport(
            job_id=job.id,
            mirror_id=mirror.id,
            playlist_name=job.playlist_name,
            yt_playlist_url=yt_url,
            status="COMPLETED" if unmatched == 0 else "COMPLETED_WITH_WARNINGS",
            total_source_tracks=job.total_tracks,
            total_synced_tracks=job.synced_tracks,
            exact_matches=exact,
            high_matches=high,
            probable_matches=probable,
            ambiguous_matches=ambiguous,
            unmatched_tracks=unmatched,
            manual_overrides_applied=sum(1 for item in matched_items if item.get("is_manual_override")),
            duration_seconds=duration_sec
        )

        DatabaseManager.save_sync_history(
            job_id=job.id,
            mirror_id=mirror.id,
            playlist_name=job.playlist_name,
            yt_playlist_id=yt_pl_id,
            total_tracks=job.total_tracks,
            synced_tracks=job.synced_tracks,
            duration_sec=duration_sec,
            report=report.model_dump()
        )

        return report

    # ─── WORKFLOW 2: DELTA SYNC (Update Existing Mirror) ─────────────────
    def generate_sync_plan(
        self,
        mirror_id: str,
        source_type: Optional[str] = None,
        file_path: Optional[str] = None,
        destination: Optional[MusicDestination] = None,
        source: Optional[MusicSource] = None
    ) -> SyncPlan:
        mirror = DatabaseManager.get_mirror(mirror_id)
        if not mirror:
            raise ValueError(f"Mirrored playlist {mirror_id} not found.")

        mode = source_type or mirror.source_type
        if source:
            sp_pl = source.get_playlist_tracks(mirror.spotify_id or mirror.name)
        else:
            sp_provider = UnifiedSpotifyProvider(default_source_type=mode, file_path=file_path)
            sp_pl = sp_provider.get_playlist_tracks(mirror.spotify_id or mirror.name, source_type=mode, file_path=file_path)

        dest = destination or YouTubeMusicDestination()
        yt_pl = dest.get_playlist(mirror.yt_playlist_id)
        if yt_pl is None:
            logger.warning(f"Destination YouTube Music playlist '{mirror.yt_playlist_name}' (ID: {mirror.yt_playlist_id}) was deleted or not found.")
            DatabaseManager.delete_mirror(mirror.id)
            return None

        dest_tracks = yt_pl.tracks
        manifest = DatabaseManager.get_manifest_tracks(mirror.id)

        plan = SyncPlanner.generate_plan(
            source_playlist=sp_pl,
            dest_tracks=dest_tracks,
            manifest_tracks=manifest,
            mirror=mirror
        )

        self.active_plans[plan.id] = plan
        return plan

    def execute_delta_sync(
        self,
        mirror_id: str,
        plan: Optional[SyncPlan] = None,
        destination: Optional[MusicDestination] = None,
        source: Optional[MusicSource] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> SyncReport:
        mirror = DatabaseManager.get_mirror(mirror_id)
        if not mirror:
            raise ValueError(f"Mirrored playlist {mirror_id} not found.")

        dest = destination or YouTubeMusicDestination()
        active_plan = plan or self.generate_sync_plan(mirror_id, destination=dest, source=source)
        if not active_plan:
            raise ValueError(f"Destination playlist for mirror '{mirror.name}' was deleted on YouTube Music. Please run a full transfer to recreate it.")

        job_id = str(uuid.uuid4())[:8]
        new_items_to_add = [it for it in active_plan.items if it.action == PlanAction.ADD_TRACK and it.is_accepted]
        removals_to_process = [it for it in active_plan.items if it.action == PlanAction.REMOVE_TRACK and it.is_accepted]

        job = SyncJob(
            id=job_id,
            mirror_id=mirror.id,
            playlist_name=mirror.name,
            spotify_url_or_id=mirror.spotify_id or mirror.name,
            yt_playlist_id=mirror.yt_playlist_id,
            mode=SyncMode.INCREMENTAL,
            status=JobStatus.SYNCING,
            total_tracks=len(new_items_to_add)
        )
        self.active_jobs[job_id] = job
        DatabaseManager.create_job(
            job.id, job.playlist_name, job.spotify_url_or_id,
            job.mode.value, job.total_tracks, mirror_id=mirror.id, yt_playlist_id=mirror.yt_playlist_id
        )

        logger.info(f"--> Starting delta sync for mirror '{mirror.name}': {len(new_items_to_add)} additions, {len(removals_to_process)} removals.")
        start_time = time.time()

        # If zero changes detected
        if not new_items_to_add and not removals_to_process:
            DatabaseManager.update_mirror_status(mirror.id, MirrorStatus.IN_SYNC.value, delta_added=0, delta_removed=0, touch_synced=True)
            report = SyncReport(
                job_id=job_id,
                mirror_id=mirror.id,
                playlist_name=mirror.name,
                yt_playlist_url=f"https://music.youtube.com/playlist?list={mirror.yt_playlist_id}",
                status="COMPLETED",
                total_source_tracks=active_plan.source_track_count,
                total_synced_tracks=0,
                already_synchronized_tracks=active_plan.unchanged_count,
                duration_seconds=round(time.time() - start_time, 2)
            )
            DatabaseManager.update_job_status(job.id, JobStatus.COMPLETED.value)
            return report

        # Match and collect newly added video IDs
        matched_new_manifest = []
        video_ids_to_add = []
        unmatched_count = 0
        exact_count = 0
        high_count = 0
        probable_count = 0
        ambiguous_count = 0

        # Fetch existing tracks on YT Music to guarantee 100% idempotency
        existing_yt_pl = dest.get_playlist(mirror.yt_playlist_id)
        existing_yt_vids: Set[str] = {t.id for t in existing_yt_pl.tracks} if existing_yt_pl else set()

        for idx, item in enumerate(new_items_to_add, 1):
            if self.cancel_flags.get(job_id, False):
                job.status = JobStatus.CANCELLED
                DatabaseManager.update_job_status(job.id, JobStatus.CANCELLED.value)
                return SyncReport(job_id=job.id, playlist_name=mirror.name, yt_playlist_url="", status="CANCELLED")

            track = item.source_track
            query_key = clean_query_string(track.artist, track.name)

            # Check override
            match_res = OverrideManager.get_override_match(track)
            if not match_res:
                cached = DatabaseManager.get_cached_match(query_key)
                if cached:
                    cand = CandidateTrack(
                        video_id=cached["video_id"],
                        title=cached["title"] or track.name,
                        artist=cached["artist"] or track.artist,
                        duration_seconds=cached["duration_seconds"],
                        result_type=cached["result_type"]
                    )
                    match_res = MatchResult(
                        source_track=track,
                        matched_track=cand,
                        confidence_score=cached.get("confidence_score", 1.0),
                        confidence_tier=ConfidenceTier.EXACT,
                        rationale="Loaded from cache",
                        is_accepted=True
                    )

            if not match_res:
                candidates = dest.search_candidates(track, limit=5)
                match_res = MatchingEngine.match_track(track, candidates)
                if match_res.matched_track and match_res.confidence_tier in [ConfidenceTier.EXACT, ConfidenceTier.HIGH]:
                    cand = match_res.matched_track
                    DatabaseManager.save_cached_match(
                        query=query_key,
                        video_id=cand.video_id,
                        title=cand.title,
                        artist=cand.artist,
                        duration=cand.duration_seconds,
                        result_type=cand.result_type,
                        score=match_res.confidence_score,
                        isrc=track.isrc
                    )

            if match_res.matched_track:
                vid = match_res.matched_track.video_id
                if vid not in existing_yt_vids and vid not in video_ids_to_add:
                    video_ids_to_add.append(vid)

                matched_new_manifest.append({
                    "spotify_uri": track.uri or f"spotify:track:{track.id}",
                    "spotify_name": track.name,
                    "spotify_artist": track.artist,
                    "spotify_album": track.album,
                    "spotify_duration": track.duration_seconds,
                    "yt_video_id": vid,
                    "yt_title": match_res.matched_track.title,
                    "yt_artist": match_res.matched_track.artist
                })

                if match_res.confidence_tier == ConfidenceTier.EXACT:
                    exact_count += 1
                elif match_res.confidence_tier == ConfidenceTier.HIGH:
                    high_count += 1
                elif match_res.confidence_tier == ConfidenceTier.PROBABLE:
                    probable_count += 1
                elif match_res.confidence_tier == ConfidenceTier.AMBIGUOUS:
                    ambiguous_count += 1
            else:
                unmatched_count += 1

            if progress_callback:
                try:
                    progress_callback(idx, len(new_items_to_add), f"{track.artist} - {track.name}")
                except Exception:
                    pass

        # Upload new video IDs in chunks
        if video_ids_to_add:
            logger.info(f"--> Uploading {len(video_ids_to_add)} delta tracks to YouTube Music playlist '{mirror.yt_playlist_id}'...")
            dest.add_tracks_to_playlist(mirror.yt_playlist_id, video_ids_to_add)
            job.synced_tracks = len(video_ids_to_add)

        # Process Removals if any
        if removals_to_process:
            removal_vids = [r.destination_video_id for r in removals_to_process if r.destination_video_id]
            logger.info(f"--> Reconciling {len(removal_vids)} removals on destination playlist...")
            DatabaseManager.remove_manifest_tracks(mirror.id, removal_vids)
            job.removed_tracks = len(removal_vids)

        # Save new additions to track manifest
        if matched_new_manifest:
            DatabaseManager.save_manifest_tracks(mirror.id, matched_new_manifest)

        # Update Mirror State
        new_yt_count = (existing_yt_pl.track_count if existing_yt_pl else 0) + len(video_ids_to_add) - len(removals_to_process)
        DatabaseManager.update_mirror_status(
            mirror.id,
            status=MirrorStatus.IN_SYNC.value,
            spotify_count=active_plan.source_track_count,
            yt_count=new_yt_count,
            delta_added=0,
            delta_removed=0,
            touch_synced=True
        )

        duration_sec = round(time.time() - start_time, 2)
        job.status = JobStatus.COMPLETED
        DatabaseManager.update_job_status(job.id, JobStatus.COMPLETED.value, synced_count=job.synced_tracks, removed_count=job.removed_tracks)

        yt_url = f"https://music.youtube.com/playlist?list={mirror.yt_playlist_id}"
        report = SyncReport(
            job_id=job.id,
            mirror_id=mirror.id,
            playlist_name=mirror.name,
            yt_playlist_url=yt_url,
            status="COMPLETED" if unmatched_count == 0 else "COMPLETED_WITH_WARNINGS",
            total_source_tracks=active_plan.source_track_count,
            total_synced_tracks=job.synced_tracks,
            already_synchronized_tracks=active_plan.unchanged_count,
            total_removed_tracks=job.removed_tracks,
            exact_matches=exact_count,
            high_matches=high_count,
            probable_matches=probable_count,
            ambiguous_matches=ambiguous_count,
            unmatched_tracks=unmatched_count,
            manual_overrides_applied=0,
            duration_seconds=duration_sec
        )

        DatabaseManager.save_sync_history(
            job_id=job.id,
            mirror_id=mirror.id,
            playlist_name=mirror.name,
            yt_playlist_id=mirror.yt_playlist_id,
            total_tracks=active_plan.source_track_count,
            synced_tracks=job.synced_tracks,
            duration_sec=duration_sec,
            report=report.model_dump()
        )

        logger.info(f"--> Delta sync complete for '{mirror.name}': {job.synced_tracks} added, {job.removed_tracks} removed, 0 duplicates.")
        return report

sync_service = SyncService()
