import re
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.core.models import (
    MirroredPlaylist,
    MirrorTrackManifest,
    MirrorStatus,
    MirrorDiscoveryCandidate,
    Playlist,
    Track,
    SyncPolicy
)
from src.core.database import DatabaseManager, get_db
from src.domain.discovery_engine import MirrorDiscoveryEngine
from src.providers.spotify.unified_provider import UnifiedSpotifyProvider
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.providers.base import MusicDestination
from src.providers.factory import DestinationRegistry
from src.domain.normalizer import clean_query_string
from src.core.logger import logger

class MirrorService:
    @staticmethod
    def list_mirrors() -> List[MirroredPlaylist]:
        return DatabaseManager.list_mirrors()

    @staticmethod
    def get_mirror(mirror_id: str) -> Optional[MirroredPlaylist]:
        return DatabaseManager.get_mirror(mirror_id)

    @staticmethod
    def get_manifest(mirror_id: str) -> List[MirrorTrackManifest]:
        return DatabaseManager.get_manifest_tracks(mirror_id)

    @staticmethod
    def unlink_mirror(mirror_id: str):
        DatabaseManager.delete_mirror(mirror_id)
        logger.info(f"Unlinked mirror {mirror_id}")

    @staticmethod
    def update_policy(mirror_id: str, additions: str = None, removals: str = None, matching: str = None):
        DatabaseManager.update_mirror_policy(mirror_id, additions, removals, matching)

    @classmethod
    def get_unified_dashboard_playlists(
        cls,
        source_type: Optional[str] = None,
        file_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Returns a single unified view of all Spotify playlists with their live
        mirror status, detected YouTube matches, track counts, and accurate deltas.
        """
        pref_mode = DatabaseManager.get_app_preference("active_source_mode", "api")
        effective_source_type = source_type or pref_mode
        sp_provider = UnifiedSpotifyProvider(default_source_type=effective_source_type, file_path=file_path)
        source_info = sp_provider.get_active_source_info()
        spotify_playlists = sp_provider.get_playlists(source_type=effective_source_type, file_path=file_path)

        mirrors = DatabaseManager.list_mirrors()
        mirrors_by_name = {m.name.lower().strip(): m for m in mirrors}
        mirrors_by_sp_id = {m.spotify_id: m for m in mirrors if m.spotify_id}

        dest_name = DestinationRegistry.get_active_destination_name()
        dest = DestinationRegistry.get_destination(dest_name)
        dest_available = dest.is_available() if hasattr(dest, "is_available") else False
        yt_library = []
        if dest_available:
            try:
                yt_library = dest.get_library_playlists(limit=200)
            except Exception as e:
                logger.warning(f"Could not load {dest_name} library for discovery: {e}")

        # Index destination playlists by playlistId and cleaned title
        yt_by_id = {}
        yt_by_name = {}
        for y in yt_library:
            pid = y.get("playlistId") or y.get("id")
            if pid:
                yt_by_id[str(pid)] = y
            title = y.get("title", "").strip()
            if title:
                yt_by_name[title.lower()] = y

        dashboard_items = []
        total_in_sync = 0
        total_changes = 0
        total_delta_tracks = 0
        total_discovered_matches = 0

        for sp_pl in spotify_playlists:
            name_clean = sp_pl.name.lower().strip()
            sp_id = sp_pl.id
            sp_track_count = sp_pl.track_count

            # 1. Check if playlist is already an active mirror
            mirror = mirrors_by_name.get(name_clean) or (mirrors_by_sp_id.get(sp_id) if sp_id else None)

            if mirror:
                manifest = DatabaseManager.get_manifest_tracks(mirror.id)
                
                # If manifest was empty, populate from destination and Spotify tracks
                if not manifest:
                    cls._backfill_mirror_manifest(mirror, sp_provider, effective_source_type, file_path)
                    manifest = DatabaseManager.get_manifest_tracks(mirror.id)

                manifest_uris = {m.spotify_uri for m in manifest}

                if sp_pl.tracks:
                    sp_track_count = len(sp_pl.tracks)
                    sp_uris = {t.uri or f"spotify:track:{t.id}" for t in sp_pl.tracks}
                else:
                    sp_uris = manifest_uris

                # Check Spotify vs local manifest additions/removals
                new_additions = len(sp_uris - manifest_uris) if manifest_uris else 0
                new_removals = len(manifest_uris - sp_uris) if (manifest_uris and sp_pl.tracks) else 0

                # Resolve live YouTube playlist track count
                matched_yt = yt_by_id.get(mirror.yt_playlist_id) or yt_by_name.get(name_clean)
                yt_cnt = mirror.yt_track_count or sp_track_count
                if matched_yt:
                    cnt_raw = str(matched_yt.get("count", "0")).replace(",", "").replace(".", "")
                    try:
                        yt_cnt = int(re.search(r"\d+", cnt_raw).group(0)) if re.search(r"\d+", cnt_raw) else yt_cnt
                    except Exception:
                        pass

                # If destination has fewer tracks than source, reflect the missing tracks in delta
                if yt_cnt < sp_track_count:
                    new_additions = max(new_additions, sp_track_count - yt_cnt)

                status = "IN_SYNC" if (new_additions == 0 and new_removals == 0) else "CHANGES_DETECTED"
                if status == "IN_SYNC":
                    total_in_sync += 1
                else:
                    total_changes += 1
                    total_delta_tracks += new_additions

                # Update mirror row in DB with live counts and deltas
                DatabaseManager.update_mirror_status(
                    mirror.id,
                    status=status,
                    spotify_count=sp_track_count,
                    yt_count=yt_cnt,
                    delta_added=new_additions,
                    delta_removed=new_removals
                )

                dashboard_items.append({
                    "id": mirror.id,
                    "mirror_id": mirror.id,
                    "name": sp_pl.name,
                    "description": sp_pl.description,
                    "spotify_id": sp_pl.id,
                    "spotify_uri": sp_pl.uri,
                    "spotify_track_count": sp_track_count,
                    "yt_playlist_id": mirror.yt_playlist_id,
                    "yt_playlist_name": mirror.yt_playlist_name,
                    "yt_track_count": yt_cnt,
                    "status": status,
                    "delta_added": new_additions,
                    "delta_removed": new_removals,
                    "last_synced_at": mirror.last_synced_at.isoformat() if mirror.last_synced_at else None,
                    "is_mirrored": True,
                    "auto_match_found": False,
                    "match_confidence": 1.0
                })

            else:
                # 2. Check if a matching playlist exists in YouTube Music library
                matched_yt = yt_by_name.get(name_clean)
                auto_match_id = None
                auto_match_name = None
                yt_cnt = 0
                match_confidence = 0.0

                if matched_yt:
                    auto_match_id = matched_yt.get("playlistId")
                    auto_match_name = matched_yt.get("title")
                    cnt_raw = str(matched_yt.get("count", "0")).replace(",", "").replace(".", "")
                    try:
                        yt_cnt = int(re.search(r"\d+", cnt_raw).group(0)) if re.search(r"\d+", cnt_raw) else 0
                    except Exception:
                        yt_cnt = 0

                    # Calculate match confidence and estimated delta
                    total_discovered_matches += 1
                    delta_est = max(0, sp_track_count - yt_cnt)
                    if sp_track_count > 0 and yt_cnt > 0:
                        count_ratio = min(sp_track_count, yt_cnt) / max(sp_track_count, yt_cnt)
                        match_confidence = round(0.5 + (count_ratio * 0.5), 2)
                    else:
                        match_confidence = 0.90

                    if delta_est > 0:
                        total_changes += 1
                        total_delta_tracks += delta_est
                    else:
                        total_in_sync += 1

                dashboard_items.append({
                    "id": sp_pl.id or sp_pl.name,
                    "mirror_id": None,
                    "name": sp_pl.name,
                    "description": sp_pl.description,
                    "spotify_id": sp_pl.id,
                    "spotify_uri": sp_pl.uri,
                    "spotify_track_count": sp_track_count,
                    "yt_playlist_id": auto_match_id,
                    "yt_playlist_name": auto_match_name,
                    "yt_track_count": yt_cnt,
                    "status": "MATCH_DETECTED" if auto_match_id else "NOT_MIRRORED",
                    "delta_added": max(0, sp_track_count - yt_cnt) if auto_match_id else 0,
                    "delta_removed": 0,
                    "last_synced_at": None,
                    "is_mirrored": False,
                    "auto_match_found": bool(auto_match_id),
                    "match_confidence": match_confidence
                })

        return {
            "source_info": source_info,
            "effective_source_type": effective_source_type,
            "playlists": dashboard_items,
            "yt_library": [
                {
                    "playlist_id": y.get("playlistId"),
                    "title": y.get("title"),
                    "count": y.get("count", 0),
                    "is_mirrored": y.get("playlistId") in {m.yt_playlist_id for m in mirrors}
                }
                for y in yt_library
            ],
            "summary": {
                "total_playlists": len(dashboard_items),
                "mirrored_count": len([i for i in dashboard_items if i["is_mirrored"]]),
                "discovered_matches_count": total_discovered_matches,
                "in_sync_count": total_in_sync,
                "changes_count": total_changes,
                "total_delta_tracks": total_delta_tracks,
                "needs_transfer_count": len([i for i in dashboard_items if not i["is_mirrored"] and not i["auto_match_found"]])
            }
        }

    @classmethod
    def _backfill_mirror_manifest(
        cls,
        mirror: MirroredPlaylist,
        sp_provider: UnifiedSpotifyProvider,
        source_type: str,
        file_path: Optional[str] = None
    ):
        """
        Populate initial manifest by matching Spotify tracks with actual YouTube destination playlist tracks.
        """
        try:
            try:
                sp_pl = sp_provider.get_playlist_tracks(mirror.name or mirror.spotify_id, source_type=source_type, file_path=file_path)
            except TypeError:
                sp_pl = sp_provider.get_playlist_tracks(mirror.name or mirror.spotify_id)
            dest_name = mirror.destination or "youtube"
            dest = DestinationRegistry.get_destination(dest_name)
            dest_pl = dest.get_playlist(mirror.yt_playlist_id) if (hasattr(dest, "is_available") and dest.is_available()) else None
            dest_tracks = dest_pl.tracks if dest_pl else []

            # Index destination tracks by normalized query
            dest_by_query = {}
            for dt in dest_tracks:
                q = clean_query_string(dt.artist, dt.name).lower()
                if q:
                    dest_by_query[q] = dt

            manifest_items = []
            for t in sp_pl.tracks:
                q = clean_query_string(t.artist, t.name).lower()
                matched_dest_track = dest_by_query.get(q)
                cached = DatabaseManager.get_cached_match(q, destination=dest_name)

                video_id = None
                dest_title = None
                dest_artist = None

                if matched_dest_track:
                    video_id = matched_dest_track.id
                    dest_title = matched_dest_track.name
                    dest_artist = matched_dest_track.artist
                    # Cache the discovered match
                    DatabaseManager.save_cached_match(
                        query=q,
                        video_id=video_id,
                        title=dest_title,
                        artist=dest_artist,
                        duration=matched_dest_track.duration_seconds,
                        result_type="song",
                        score=1.0,
                        isrc=t.isrc,
                        destination=dest_name
                    )
                elif cached:
                    video_id = cached["video_id"]
                    dest_title = cached.get("title") or t.name
                    dest_artist = cached.get("artist") or t.artist

                # Guard against cross-destination ID contamination
                if video_id:
                    if dest_name == "deezer" and not str(video_id).strip().isdigit():
                        continue
                    manifest_items.append({
                        "spotify_uri": t.uri or f"spotify:track:{t.id}",
                        "spotify_name": t.name,
                        "spotify_artist": t.artist,
                        "spotify_album": t.album,
                        "spotify_duration": t.duration_seconds,
                        "yt_video_id": str(video_id),
                        "yt_title": dest_title or t.name,
                        "yt_artist": dest_artist or t.artist
                    })

            if manifest_items:
                DatabaseManager.save_manifest_tracks(mirror.id, manifest_items)
                logger.info(f"Populated {len(manifest_items)} / {len(sp_pl.tracks)} manifest tracks for mirror '{mirror.name}'")

        except Exception as e:
            logger.warning(f"Could not backfill manifest for {mirror.name}: {e}")

    @classmethod
    def add_by_url(
        cls,
        spotify_url_or_id: str,
        yt_playlist_id: Optional[str] = None
    ) -> MirroredPlaylist:
        """Add and mirror any Spotify playlist directly via URL or ID."""
        sp_provider = UnifiedSpotifyProvider()
        sp_pl = sp_provider.get_playlist_tracks(spotify_url_or_id)
        if not sp_pl or not sp_pl.tracks:
            raise ValueError(f"Could not load tracks from Spotify playlist: '{spotify_url_or_id}'. Ensure the playlist is public or your connection is active.")

        yt_dest = YouTubeMusicDestination()
        if not yt_playlist_id:
            yt_library = yt_dest.get_library_playlists() if yt_dest.is_available() else []
            name_clean = sp_pl.name.lower().strip()
            for y in yt_library:
                if y.get("title", "").lower().strip() == name_clean:
                    yt_playlist_id = y.get("playlistId")
                    break

        if not yt_playlist_id:
            if not yt_dest.is_available():
                raise ValueError("YouTube Music is not connected. Please connect your YouTube session in Connections & Auth.")
            yt_playlist_id = yt_dest.create_playlist(sp_pl.name, description=f"Synced from Spotify: {sp_pl.name}")

        return cls.link_existing_mirror(
            spotify_identifier=spotify_url_or_id,
            yt_playlist_id=yt_playlist_id,
            source_type="api" if sp_pl.source_type == "api" else "web"
        )

    @classmethod
    def link_existing_mirror(
        cls,
        spotify_identifier: str,
        yt_playlist_id: str,
        source_type: str = "export",
        file_path: Optional[str] = None,
        destination: Optional[MusicDestination] = None,
        destination_name: Optional[str] = None
    ) -> MirroredPlaylist:
        """
        Link an existing Spotify playlist with an existing destination playlist,
        fetching existing tracks from destination to populate the manifest accurately.
        """
        sp_provider = UnifiedSpotifyProvider(default_source_type=source_type, file_path=file_path)
        sp_pl = sp_provider.get_playlist_tracks(spotify_identifier, source_type=source_type, file_path=file_path)
        
        dest = destination or DestinationRegistry.get_destination(destination_name)
        dest_canonical = destination_name or ("deezer" if "deezer" in dest.__class__.__name__.lower() else "youtube")
        
        live_pl = dest.get_playlist(yt_playlist_id)
        dest_name = live_pl.name if live_pl else sp_pl.name
        dest_track_count = len(live_pl.tracks) if live_pl else len(sp_pl.tracks)

        # Check if mirror already exists
        existing = DatabaseManager.get_mirror_by_yt(yt_playlist_id)
        if existing:
            cls._backfill_mirror_manifest(existing, sp_provider, source_type, file_path)
            return existing

        mirror = DatabaseManager.create_mirror(
            name=sp_pl.name,
            yt_playlist_id=yt_playlist_id,
            yt_playlist_name=dest_name,
            spotify_id=sp_pl.id,
            spotify_uri=sp_pl.uri,
            source_type=source_type,
            source_snapshot_id=sp_pl.snapshot_id,
            spotify_track_count=len(sp_pl.tracks),
            yt_track_count=dest_track_count,
            destination=dest_canonical
        )

        cls._backfill_mirror_manifest(mirror, sp_provider, source_type, file_path)
        logger.info(f"Successfully established mirror link '{mirror.name}' ↔ '{yt_playlist_id}' ({dest_canonical})")
        return mirror

    @classmethod
    def auto_link_all_discovered(cls, source_type: str = "export", file_path: Optional[str] = None) -> List[MirroredPlaylist]:
        """Auto-link all detected matches in YouTube Music library."""
        dashboard = cls.get_unified_dashboard_playlists(source_type=source_type, file_path=file_path)
        linked = []
        for p in dashboard.get("playlists", []):
            if not p.get("is_mirrored") and p.get("auto_match_found") and p.get("yt_playlist_id"):
                try:
                    m = cls.link_existing_mirror(
                        spotify_identifier=p["name"],
                        yt_playlist_id=p["yt_playlist_id"],
                        source_type=source_type,
                        file_path=file_path
                    )
                    linked.append(m)
                except Exception as e:
                    logger.error(f"Failed to auto-link '{p.get('name')}': {e}")
        return linked

    @classmethod
    def refresh_mirror_deltas(cls, source_type: str = "export", file_path: Optional[str] = None) -> List[MirroredPlaylist]:
        sp_provider = UnifiedSpotifyProvider(default_source_type=source_type, file_path=file_path)
        mirrors = DatabaseManager.list_mirrors()
        for mirror in mirrors:
            try:
                dest = DestinationRegistry.get_destination(mirror.destination or "youtube")
                sp_pl = sp_provider.get_playlist_tracks(mirror.name or mirror.spotify_id, source_type=mirror.source_type, file_path=file_path)
                manifest = DatabaseManager.get_manifest_tracks(mirror.id)
                if not manifest:
                    cls._backfill_mirror_manifest(mirror, sp_provider, mirror.source_type, file_path)
                    manifest = DatabaseManager.get_manifest_tracks(mirror.id)

                manifest_uris = {m.spotify_uri for m in manifest}
                sp_tracks = sp_pl.tracks
                sp_uris = {t.uri or f"spotify:track:{t.id}" for t in sp_tracks}

                new_additions = len(sp_uris - manifest_uris)
                new_removals = len(manifest_uris - sp_uris)

                live_pl = dest.get_playlist(mirror.yt_playlist_id) if hasattr(dest, "get_playlist") else None
                dest_cnt = live_pl.track_count if live_pl else (mirror.yt_track_count or len(sp_tracks))

                if dest_cnt < len(sp_tracks):
                    new_additions = max(new_additions, len(sp_tracks) - dest_cnt)

                status = MirrorStatus.IN_SYNC if (new_additions == 0 and new_removals == 0) else MirrorStatus.CHANGES_DETECTED

                DatabaseManager.update_mirror_status(
                    mirror.id,
                    status=status.value,
                    spotify_count=len(sp_tracks),
                    yt_count=dest_cnt,
                    delta_added=new_additions,
                    delta_removed=new_removals
                )
            except Exception as e:
                logger.error(f"Error checking deltas for mirror {mirror.name}: {e}")

        return DatabaseManager.list_mirrors()

