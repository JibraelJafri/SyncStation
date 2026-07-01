import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set, Tuple

from src.core.config import DATABASE_PATH, PROJECT_ROOT, CACHE_FILE
from src.core.models import (
    MirroredPlaylist,
    MirrorTrackManifest,
    MirrorStatus,
    ConfidenceTier,
    JobStatus,
    SyncMode,
    PlanAction,
    AdditionPolicy,
    RemovalPolicy,
    AmbiguousMatchPolicy
)

def get_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS mirrored_playlists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            spotify_id TEXT,
            spotify_uri TEXT,
            spotify_url TEXT,
            yt_playlist_id TEXT NOT NULL,
            yt_playlist_name TEXT NOT NULL,
            source_type TEXT DEFAULT 'export',
            source_snapshot_id TEXT,
            last_synced_at TIMESTAMP,
            last_sync_status TEXT DEFAULT 'NEVER_SYNCED',
            spotify_track_count INTEGER DEFAULT 0,
            yt_track_count INTEGER DEFAULT 0,
            delta_added_count INTEGER DEFAULT 0,
            delta_removed_count INTEGER DEFAULT 0,
            sync_policy_additions TEXT DEFAULT 'AUTO_ADD',
            sync_policy_removals TEXT DEFAULT 'ASK_BEFORE_REMOVE',
            sync_policy_matching TEXT DEFAULT 'ASK_REVIEW',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS mirror_track_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mirror_id TEXT NOT NULL,
            spotify_uri TEXT NOT NULL,
            spotify_name TEXT NOT NULL,
            spotify_artist TEXT NOT NULL,
            spotify_album TEXT DEFAULT '',
            spotify_duration REAL DEFAULT 0.0,
            yt_video_id TEXT NOT NULL,
            yt_title TEXT,
            yt_artist TEXT,
            position INTEGER DEFAULT 0,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (mirror_id) REFERENCES mirrored_playlists(id) ON DELETE CASCADE,
            UNIQUE(mirror_id, spotify_uri)
        );

        CREATE TABLE IF NOT EXISTS sync_jobs (
            id TEXT PRIMARY KEY,
            mirror_id TEXT,
            playlist_name TEXT NOT NULL,
            spotify_url_or_id TEXT NOT NULL,
            yt_playlist_id TEXT,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            total_tracks INTEGER DEFAULT 0,
            processed_tracks INTEGER DEFAULT 0,
            matched_tracks INTEGER DEFAULT 0,
            synced_tracks INTEGER DEFAULT 0,
            removed_tracks INTEGER DEFAULT 0,
            skipped_tracks INTEGER DEFAULT 0,
            failed_tracks INTEGER DEFAULT 0,
            current_track_name TEXT DEFAULT '',
            current_thumbnail_url TEXT,
            eta_seconds INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sync_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            track_index INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            source_artist TEXT NOT NULL,
            source_album TEXT DEFAULT '',
            source_duration REAL DEFAULT 0.0,
            source_uri TEXT DEFAULT '',
            matched_video_id TEXT,
            matched_title TEXT,
            matched_artist TEXT,
            confidence_score REAL DEFAULT 0.0,
            confidence_tier TEXT DEFAULT 'NO_MATCH',
            rationale TEXT DEFAULT '',
            status TEXT DEFAULT 'PENDING',
            is_manual_override INTEGER DEFAULT 0,
            is_accepted INTEGER DEFAULT 1,
            error TEXT,
            FOREIGN KEY (job_id) REFERENCES sync_jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_query TEXT UNIQUE NOT NULL,
            source_uri TEXT,
            target_video_id TEXT NOT NULL,
            target_title TEXT,
            target_artist TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS matches_cache (
            query_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            duration_seconds REAL DEFAULT 0.0,
            result_type TEXT DEFAULT 'song',
            confidence_score REAL DEFAULT 1.0,
            isrc TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            mirror_id TEXT,
            playlist_name TEXT NOT NULL,
            yt_playlist_id TEXT NOT NULL,
            total_tracks INTEGER DEFAULT 0,
            synced_tracks INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0.0,
            report_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS snapshots_meta (
            file_name TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_size_kb REAL DEFAULT 0.0,
            modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            export_date TEXT,
            playlist_count INTEGER DEFAULT 0,
            total_tracks INTEGER DEFAULT 0,
            user_id TEXT,
            is_active INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_mirrors_spotify_id ON mirrored_playlists(spotify_id);
        CREATE INDEX IF NOT EXISTS idx_mirrors_yt_id ON mirrored_playlists(yt_playlist_id);
        CREATE INDEX IF NOT EXISTS idx_manifest_mirror ON mirror_track_manifests(mirror_id);
        CREATE INDEX IF NOT EXISTS idx_manifest_vid ON mirror_track_manifests(yt_video_id);
        CREATE INDEX IF NOT EXISTS idx_sync_items_job_id ON sync_items(job_id);
        CREATE INDEX IF NOT EXISTS idx_matches_cache_query ON matches_cache(query_key);
        CREATE INDEX IF NOT EXISTS idx_overrides_query ON user_overrides(source_query);
        CREATE INDEX IF NOT EXISTS idx_sync_jobs_status ON sync_jobs(status);
        """)

        # Ensure any missing columns from older versions are cleanly added
        _run_migrations(conn)

    # Automatically migrate legacy cache and history into mirrored_playlists and matches_cache
    _migrate_legacy_data()

def _run_migrations(conn: sqlite3.Connection):
    """Safely adds new columns if existing database had older schema."""
    cursor = conn.cursor()
    
    # Check sync_jobs columns
    cursor.execute("PRAGMA table_info(sync_jobs)")
    cols = {row["name"] for row in cursor.fetchall()}
    if "mirror_id" not in cols:
        conn.execute("ALTER TABLE sync_jobs ADD COLUMN mirror_id TEXT")
    if "removed_tracks" not in cols:
        conn.execute("ALTER TABLE sync_jobs ADD COLUMN removed_tracks INTEGER DEFAULT 0")
    if "current_thumbnail_url" not in cols:
        conn.execute("ALTER TABLE sync_jobs ADD COLUMN current_thumbnail_url TEXT")
    if "eta_seconds" not in cols:
        conn.execute("ALTER TABLE sync_jobs ADD COLUMN eta_seconds INTEGER")

    # Check matches_cache columns
    cursor.execute("PRAGMA table_info(matches_cache)")
    cache_cols = {row["name"] for row in cursor.fetchall()}
    if "isrc" not in cache_cols:
        conn.execute("ALTER TABLE matches_cache ADD COLUMN isrc TEXT")

    # Check sync_history columns
    cursor.execute("PRAGMA table_info(sync_history)")
    hist_cols = {row["name"] for row in cursor.fetchall()}
    if "mirror_id" not in hist_cols:
        conn.execute("ALTER TABLE sync_history ADD COLUMN mirror_id TEXT")

    # Clean up non-existent snapshots or temp test snapshots
    conn.execute("DELETE FROM snapshots_meta WHERE file_path LIKE '%Temp%' OR file_path LIKE '%pytest%'")
    conn.execute("UPDATE snapshots_meta SET is_active = 1 WHERE file_name LIKE 'spotify-export%'")

def _migrate_legacy_data():
    """Migrate legacy song query cache into matches_cache table."""
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)

            song_queries = legacy.get("song_queries", {})
            if song_queries:
                with get_db() as conn:
                    cur = conn.cursor()
                    for query, vid in song_queries.items():
                        if query and vid:
                            conn.execute("""
                            INSERT OR IGNORE INTO matches_cache (query_key, video_id, title, artist, confidence_score)
                            VALUES (?, ?, '', '', 1.0)
                            """, (query.lower().strip(), vid))
    except Exception:
        pass


class DatabaseManager:
    # ─── MIRRORED PLAYLISTS DAO ──────────────────────────────────────────
    @staticmethod
    def create_mirror(
        name: str,
        yt_playlist_id: str,
        yt_playlist_name: str,
        spotify_id: str = "",
        spotify_uri: str = "",
        spotify_url: str = "",
        source_type: str = "export",
        source_snapshot_id: str = None,
        spotify_track_count: int = 0,
        yt_track_count: int = 0,
        policy_additions: str = "AUTO_ADD",
        policy_removals: str = "ASK_BEFORE_REMOVE",
        policy_matching: str = "ASK_REVIEW"
    ) -> MirroredPlaylist:
        mirror_id = str(uuid.uuid4())[:8]
        with get_db() as conn:
            conn.execute("""
            INSERT INTO mirrored_playlists (
                id, name, spotify_id, spotify_uri, spotify_url,
                yt_playlist_id, yt_playlist_name, source_type, source_snapshot_id,
                last_sync_status, spotify_track_count, yt_track_count,
                sync_policy_additions, sync_policy_removals, sync_policy_matching,
                last_synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN_SYNC', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (
                mirror_id, name, spotify_id, spotify_uri, spotify_url,
                yt_playlist_id, yt_playlist_name, source_type, source_snapshot_id,
                spotify_track_count, yt_track_count,
                policy_additions, policy_removals, policy_matching
            ))
        return DatabaseManager.get_mirror(mirror_id)

    @staticmethod
    def get_mirror(mirror_id: str) -> Optional[MirroredPlaylist]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM mirrored_playlists WHERE id = ?", (mirror_id,))
            row = cur.fetchone()
            if not row:
                return None
            return MirroredPlaylist(**dict(row))

    @staticmethod
    def get_mirror_by_spotify(spotify_id_or_uri: str) -> Optional[MirroredPlaylist]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM mirrored_playlists
            WHERE spotify_id = ? OR spotify_uri = ? OR name = ?
            """, (spotify_id_or_uri, spotify_id_or_uri, spotify_id_or_uri))
            row = cur.fetchone()
            return MirroredPlaylist(**dict(row)) if row else None

    @staticmethod
    def get_mirror_by_yt(yt_playlist_id: str) -> Optional[MirroredPlaylist]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM mirrored_playlists WHERE yt_playlist_id = ?", (yt_playlist_id,))
            row = cur.fetchone()
            return MirroredPlaylist(**dict(row)) if row else None

    @staticmethod
    def list_mirrors() -> List[MirroredPlaylist]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM mirrored_playlists ORDER BY updated_at DESC")
            return [MirroredPlaylist(**dict(row)) for row in cur.fetchall()]

    @staticmethod
    def update_mirror_status(
        mirror_id: str,
        status: str,
        spotify_count: int = None,
        yt_count: int = None,
        delta_added: int = None,
        delta_removed: int = None,
        touch_synced: bool = False
    ):
        with get_db() as conn:
            fields = ["last_sync_status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params = [status]
            if spotify_count is not None:
                fields.append("spotify_track_count = ?")
                params.append(spotify_count)
            if yt_count is not None:
                fields.append("yt_track_count = ?")
                params.append(yt_count)
            if delta_added is not None:
                fields.append("delta_added_count = ?")
                params.append(delta_added)
            if delta_removed is not None:
                fields.append("delta_removed_count = ?")
                params.append(delta_removed)
            if touch_synced:
                fields.append("last_synced_at = CURRENT_TIMESTAMP")
            params.append(mirror_id)
            conn.execute(f"UPDATE mirrored_playlists SET {', '.join(fields)} WHERE id = ?", params)

    @staticmethod
    def update_mirror_policy(mirror_id: str, additions: str = None, removals: str = None, matching: str = None):
        with get_db() as conn:
            fields = ["updated_at = CURRENT_TIMESTAMP"]
            params = []
            if additions:
                fields.append("sync_policy_additions = ?")
                params.append(additions)
            if removals:
                fields.append("sync_policy_removals = ?")
                params.append(removals)
            if matching:
                fields.append("sync_policy_matching = ?")
                params.append(matching)
            params.append(mirror_id)
            conn.execute(f"UPDATE mirrored_playlists SET {', '.join(fields)} WHERE id = ?", params)

    @staticmethod
    def delete_mirror(mirror_id: str):
        with get_db() as conn:
            conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (mirror_id,))

    # ─── MANIFEST TRACKS DAO ─────────────────────────────────────────────
    @staticmethod
    def save_manifest_tracks(mirror_id: str, tracks: List[Dict[str, Any]]):
        with get_db() as conn:
            for idx, t in enumerate(tracks):
                conn.execute("""
                INSERT INTO mirror_track_manifests (
                    mirror_id, spotify_uri, spotify_name, spotify_artist,
                    spotify_album, spotify_duration, yt_video_id, yt_title, yt_artist, position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mirror_id, spotify_uri) DO UPDATE SET
                    yt_video_id = excluded.yt_video_id,
                    yt_title = excluded.yt_title,
                    yt_artist = excluded.yt_artist,
                    position = excluded.position,
                    synced_at = CURRENT_TIMESTAMP
                """, (
                    mirror_id,
                    t.get("spotify_uri") or f"spotify:track:{t.get('spotify_id', idx)}",
                    t.get("spotify_name") or t.get("name", ""),
                    t.get("spotify_artist") or t.get("artist", ""),
                    t.get("spotify_album") or t.get("album", ""),
                    t.get("spotify_duration") or t.get("duration_seconds", 0.0),
                    t.get("yt_video_id") or t.get("video_id", ""),
                    t.get("yt_title") or t.get("title", ""),
                    t.get("yt_artist") or t.get("artist", ""),
                    idx
                ))

    @staticmethod
    def get_manifest_tracks(mirror_id: str) -> List[MirrorTrackManifest]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM mirror_track_manifests WHERE mirror_id = ? ORDER BY position ASC", (mirror_id,))
            return [MirrorTrackManifest(**dict(row)) for row in cur.fetchall()]

    @staticmethod
    def remove_manifest_tracks(mirror_id: str, yt_video_ids: List[str]):
        if not yt_video_ids:
            return
        with get_db() as conn:
            placeholders = ",".join("?" for _ in yt_video_ids)
            conn.execute(f"DELETE FROM mirror_track_manifests WHERE mirror_id = ? AND yt_video_id IN ({placeholders})", [mirror_id] + yt_video_ids)

    # ─── OVERRIDES & MATCHES CACHE DAO ───────────────────────────────────
    @staticmethod
    def get_override(query: str, uri: str = "") -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            if uri:
                cur.execute("SELECT * FROM user_overrides WHERE source_uri = ? OR source_query = ?", (uri, query.lower().strip()))
            else:
                cur.execute("SELECT * FROM user_overrides WHERE source_query = ?", (query.lower().strip(),))
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def save_override(query: str, video_id: str, title: str = "", artist: str = "", uri: str = ""):
        with get_db() as conn:
            conn.execute("""
            INSERT INTO user_overrides (source_query, source_uri, target_video_id, target_title, target_artist)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_query) DO UPDATE SET
                source_uri = excluded.source_uri,
                target_video_id = excluded.target_video_id,
                target_title = excluded.target_title,
                target_artist = excluded.target_artist,
                created_at = CURRENT_TIMESTAMP
            """, (query.lower().strip(), uri, video_id, title, artist))

    @staticmethod
    def list_overrides() -> List[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM user_overrides ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def delete_override(override_id: int):
        with get_db() as conn:
            conn.execute("DELETE FROM user_overrides WHERE id = ?", (override_id,))

    @staticmethod
    def get_cached_match(query: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM matches_cache WHERE query_key = ?", (query.lower().strip(),))
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def save_cached_match(
        query: str,
        video_id: str,
        title: str = "",
        artist: str = "",
        duration: float = 0.0,
        result_type: str = "song",
        score: float = 1.0,
        isrc: str = None
    ):
        with get_db() as conn:
            conn.execute("""
            INSERT INTO matches_cache (query_key, video_id, title, artist, duration_seconds, result_type, confidence_score, isrc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_key) DO UPDATE SET
                video_id = excluded.video_id,
                title = excluded.title,
                artist = excluded.artist,
                duration_seconds = excluded.duration_seconds,
                result_type = excluded.result_type,
                confidence_score = excluded.confidence_score,
                isrc = COALESCE(excluded.isrc, matches_cache.isrc),
                updated_at = CURRENT_TIMESTAMP
            """, (query.lower().strip(), video_id, title, artist, duration, result_type, score, isrc))

    @staticmethod
    def batch_get_cached_matches(queries: List[str]) -> Dict[str, Dict[str, Any]]:
        if not queries:
            return {}
        normalized = [q.lower().strip() for q in queries]
        results = {}
        with get_db() as conn:
            # Query in chunks of 500
            for i in range(0, len(normalized), 500):
                chunk = normalized[i:i + 500]
                placeholders = ",".join("?" for _ in chunk)
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM matches_cache WHERE query_key IN ({placeholders})", chunk)
                for row in cur.fetchall():
                    results[row["query_key"]] = dict(row)
        return results

    # ─── SYNC JOBS & RESUMABILITY DAO ────────────────────────────────────
    @staticmethod
    def create_job(
        job_id: str,
        playlist_name: str,
        spotify_url: str,
        mode: str,
        total_tracks: int,
        mirror_id: str = None,
        yt_playlist_id: str = None
    ) -> None:
        with get_db() as conn:
            conn.execute("""
            INSERT INTO sync_jobs (
                id, mirror_id, playlist_name, spotify_url_or_id,
                yt_playlist_id, mode, status, total_tracks
            ) VALUES (?, ?, ?, ?, ?, ?, 'ANALYZING', ?)
            """, (job_id, mirror_id, playlist_name, spotify_url, yt_playlist_id, mode, total_tracks))

    @staticmethod
    def update_job_progress(
        job_id: str,
        processed: int,
        matched: int,
        current_track: str = "",
        thumbnail_url: str = None,
        eta_seconds: int = None
    ):
        with get_db() as conn:
            conn.execute("""
            UPDATE sync_jobs SET
                processed_tracks = ?,
                matched_tracks = ?,
                current_track_name = ?,
                current_thumbnail_url = COALESCE(?, current_thumbnail_url),
                eta_seconds = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (processed, matched, current_track, thumbnail_url, eta_seconds, job_id))

    @staticmethod
    def update_job_status(
        job_id: str,
        status: str,
        current_track: str = "",
        error_message: str = None,
        yt_playlist_id: str = None,
        synced_count: int = None,
        removed_count: int = None
    ):
        with get_db() as conn:
            fields = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params = [status]
            if current_track:
                fields.append("current_track_name = ?")
                params.append(current_track)
            if error_message is not None:
                fields.append("error_message = ?")
                params.append(error_message)
            if yt_playlist_id:
                fields.append("yt_playlist_id = ?")
                params.append(yt_playlist_id)
            if synced_count is not None:
                fields.append("synced_tracks = ?")
                params.append(synced_count)
            if removed_count is not None:
                fields.append("removed_tracks = ?")
                params.append(removed_count)
            params.append(job_id)
            conn.execute(f"UPDATE sync_jobs SET {', '.join(fields)} WHERE id = ?", params)

    @staticmethod
    def get_job(job_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_interrupted_jobs() -> List[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM sync_jobs
            WHERE status IN ('SYNCING', 'ANALYZING', 'PAUSED')
            ORDER BY updated_at DESC LIMIT 5
            """)
            return [dict(r) for r in cur.fetchall()]

    # ─── SYNC HISTORY & AUDIT DAO ────────────────────────────────────────
    @staticmethod
    def save_sync_history(
        job_id: str,
        playlist_name: str,
        yt_playlist_id: str,
        total_tracks: int,
        synced_tracks: int,
        duration_sec: float,
        report: dict,
        mirror_id: str = None
    ):
        with get_db() as conn:
            conn.execute("""
            INSERT INTO sync_history (
                job_id, mirror_id, playlist_name, yt_playlist_id,
                total_tracks, synced_tracks, duration_seconds, report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, mirror_id, playlist_name, yt_playlist_id,
                total_tracks, synced_tracks, duration_sec, json.dumps(report, default=str)
            ))

    @staticmethod
    def list_sync_history(limit: int = 50) -> List[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT id, job_id, mirror_id, playlist_name, yt_playlist_id,
                   total_tracks, synced_tracks, duration_seconds, created_at
            FROM sync_history ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def get_sync_history_entry(history_id: int) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_history WHERE id = ?", (history_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            res["report"] = json.loads(res["report_json"]) if res.get("report_json") else {}
            return res

    # ─── SNAPSHOT METADATA DAO ───────────────────────────────────────────
    @staticmethod
    def save_snapshot_meta(meta: Dict[str, Any]):
        p_str = str(meta.get("file_path", "")).lower()
        if "temp" in p_str or "pytest" in p_str:
            return

        with get_db() as conn:
            cur = conn.cursor()
            if meta.get("is_active"):
                cur.execute("UPDATE snapshots_meta SET is_active = 0")
            conn.execute("""
            INSERT INTO snapshots_meta (
                file_name, file_path, file_size_kb, modified_at,
                export_date, playlist_count, total_tracks, user_id, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_name) DO UPDATE SET
                file_path = excluded.file_path,
                file_size_kb = excluded.file_size_kb,
                modified_at = excluded.modified_at,
                export_date = excluded.export_date,
                playlist_count = excluded.playlist_count,
                total_tracks = excluded.total_tracks,
                user_id = excluded.user_id,
                is_active = excluded.is_active
            """, (
                meta["file_name"], meta["file_path"], meta["file_size_kb"],
                meta.get("modified_at", datetime.now(timezone.utc).isoformat()),
                meta.get("export_date"), meta.get("playlist_count", 0),
                meta.get("total_tracks", 0), meta.get("user_id"),
                int(meta.get("is_active", False))
            ))

    @staticmethod
    def list_snapshots_meta() -> List[Dict[str, Any]]:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM snapshots_meta ORDER BY modified_at DESC")
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def set_active_snapshot(file_name: str):
        with get_db() as conn:
            conn.execute("UPDATE snapshots_meta SET is_active = 0")
            conn.execute("UPDATE snapshots_meta SET is_active = 1 WHERE file_name = ?", (file_name,))

    @staticmethod
    def delete_snapshot_meta(file_name: str):
        with get_db() as conn:
            conn.execute("DELETE FROM snapshots_meta WHERE file_name = ?", (file_name,))

    @staticmethod
    def clear_all_snapshots_meta():
        with get_db() as conn:
            conn.execute("DELETE FROM snapshots_meta")

    # ─── APP PREFERENCES DAO ─────────────────────────────────────────────
    @staticmethod
    def get_app_preference(key: str, default: str = "") -> str:
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("CREATE TABLE IF NOT EXISTS app_preferences (key TEXT PRIMARY KEY, val TEXT)")
                cur.execute("SELECT val FROM app_preferences WHERE key = ?", (key,))
                row = cur.fetchone()
                return row["val"] if row else default
        except Exception:
            return default

    @staticmethod
    def set_app_preference(key: str, val: str):
        try:
            with get_db() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS app_preferences (key TEXT PRIMARY KEY, val TEXT)")
                conn.execute("INSERT INTO app_preferences (key, val) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET val = excluded.val", (key, val))
        except Exception:
            pass

