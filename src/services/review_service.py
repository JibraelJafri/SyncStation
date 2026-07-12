import csv
import json
import io
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.core.database import get_db, DatabaseManager
from src.core.models import CandidateTrack, Track
from src.domain.overrides import OverrideManager

class ReviewService:
    @staticmethod
    def get_latest_job() -> Optional[Dict[str, Any]]:
        return DatabaseManager.get_latest_job()

    @staticmethod
    def get_recent_jobs(limit: int = 10) -> List[Dict[str, Any]]:
        return DatabaseManager.list_recent_jobs(limit=limit)

    @staticmethod
    def get_audit_summary(job_id: str) -> Dict[str, Any]:
        return DatabaseManager.get_job_audit_summary(job_id)

    @staticmethod
    def get_audit_items(
        job_id: str,
        tier: Optional[str] = None,
        only_flagged: bool = False,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        return DatabaseManager.get_job_audit_items(
            job_id=job_id,
            tier=tier,
            only_flagged=only_flagged,
            limit=limit
        )

    @staticmethod
    def get_job_items(
        job_id: str,
        confidence_filter: Optional[str] = None,
        search_term: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ) -> Dict[str, Any]:
        with get_db() as conn:
            cur = conn.cursor()
            query = "SELECT * FROM sync_items WHERE job_id = ?"
            params = [job_id]

            if confidence_filter and confidence_filter != "ALL":
                query += " AND confidence_tier = ?"
                params.append(confidence_filter)

            if search_term:
                query += " AND (source_name LIKE ? OR source_artist LIKE ? OR matched_title LIKE ?)"
                like = f"%{search_term}%"
                params.extend([like, like, like])

            # Total count
            count_cur = conn.cursor()
            count_cur.execute(f"SELECT COUNT(*) FROM ({query})", params)
            total = count_cur.fetchone()[0]

            # Pagination
            query += " ORDER BY track_index ASC LIMIT ? OFFSET ?"
            params.extend([page_size, (page - 1) * page_size])

            cur.execute(query, params)
            items = []
            for row in cur.fetchall():
                d = dict(row)
                raw_alts = d.get("alternatives_json")
                if raw_alts:
                    try:
                        d["alternatives"] = json.loads(raw_alts)
                    except Exception:
                        d["alternatives"] = []
                else:
                    d["alternatives"] = []
                items.append(d)

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": items
            }

    @staticmethod
    def set_manual_override(item_id: int, video_id: str, title: str, artist: str):
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Item not found")

            track = Track(
                name=row["source_name"],
                artist=row["source_artist"],
                album=row["source_album"],
                duration_seconds=row["source_duration"],
                uri=row["source_uri"]
            )
            candidate = CandidateTrack(
                video_id=video_id,
                title=title,
                artist=artist,
                duration_seconds=row["source_duration"],
                result_type="song"
            )

            # Persist to user overrides table
            OverrideManager.set_override(track, candidate)

            # Update item in sync_items table
            conn.execute("""
            UPDATE sync_items SET
                matched_video_id = ?,
                matched_title = ?,
                matched_artist = ?,
                confidence_score = 1.0,
                confidence_tier = 'EXACT',
                rationale = 'User manual override',
                is_manual_override = 1,
                is_accepted = 1,
                status = 'SYNCED'
            WHERE id = ?
            """, (video_id, title, artist, item_id))

    @classmethod
    def apply_and_push_override(
        cls,
        item_id: int,
        video_id: str,
        title: str,
        artist: str,
        destination: Optional[Any] = None,
        destination_playlist_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Applies a manual override to the database and immediately pushes the track
        to the live destination playlist, updating the manifest and track tally.
        """
        cls.set_manual_override(item_id, video_id, title, artist)

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                return {"success": False, "pushed": False, "message": "Item not found after override."}
            item_data = dict(row)

        job_id = item_data.get("job_id")
        job = DatabaseManager.get_job(job_id) if job_id else None
        dest_pid = destination_playlist_id or (job.get("yt_playlist_id") if job else None)

        dest = destination
        if not dest and job:
            from src.providers.factory import DestinationRegistry
            dest = DestinationRegistry.get_destination(job.get("destination"))

        if not dest or not dest_pid:
            return {
                "success": True,
                "pushed": False,
                "message": "Override saved to database (no active destination playlist to push to)."
            }

        try:
            added = dest.add_tracks_to_playlist(dest_pid, [video_id])
            if added > 0:
                # Update manifest if mirror exists
                mirror = DatabaseManager.get_mirror(job.get("mirror_id")) if job and job.get("mirror_id") else None
                if not mirror and dest_pid:
                    mirror = DatabaseManager.get_mirror_by_yt(dest_pid)

                if mirror:
                    DatabaseManager.save_manifest_tracks(mirror.id, [{
                        "spotify_uri": item_data.get("source_uri"),
                        "spotify_name": item_data.get("source_name"),
                        "spotify_artist": item_data.get("source_artist"),
                        "spotify_album": item_data.get("source_album", ""),
                        "spotify_duration": item_data.get("source_duration", 0.0),
                        "yt_video_id": video_id,
                        "yt_title": title,
                        "yt_artist": artist
                    }])
                    new_yt_count = (mirror.yt_track_count or 0) + 1
                    status_val = mirror.last_sync_status.value if hasattr(mirror.last_sync_status, "value") else str(mirror.last_sync_status)
                    DatabaseManager.update_mirror_status(
                        mirror.id,
                        status=status_val,
                        yt_count=new_yt_count
                    )

                if job_id:
                    with get_db() as conn:
                        conn.execute("UPDATE sync_jobs SET synced_tracks = synced_tracks + 1 WHERE id = ?", (job_id,))

                return {
                    "success": True,
                    "pushed": True,
                    "video_id": video_id,
                    "title": title,
                    "artist": artist,
                    "message": f"Successfully pushed to live playlist ({dest_pid})."
                }
            else:
                return {
                    "success": True,
                    "pushed": False,
                    "message": f"Destination did not accept track ID '{video_id}'."
                }
        except Exception as e:
            return {
                "success": True,
                "pushed": False,
                "message": f"Saved locally, but failed to push to destination: {e}"
            }

    @staticmethod
    def toggle_item_accepted(item_id: int, accepted: bool):
        with get_db() as conn:
            conn.execute("UPDATE sync_items SET is_accepted = ? WHERE id = ?", (int(accepted), item_id))

class ExportService:
    @staticmethod
    def generate_job_csv(job_id: str) -> str:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_items WHERE job_id = ? ORDER BY track_index ASC", (job_id,))
            items = cur.fetchall()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "#", "Source Track", "Source Artist", "Source Album", "Duration (s)",
                "Matched Track", "Matched Artist", "Destination ID", "Tier", "Score",
                "Rationale", "Override", "Accepted"
            ])

            for item in items:
                writer.writerow([
                    item["track_index"],
                    item["source_name"],
                    item["source_artist"],
                    item["source_album"],
                    item["source_duration"],
                    item["matched_title"] or "",
                    item["matched_artist"] or "",
                    item["matched_video_id"] or "",
                    item["confidence_tier"],
                    f"{item['confidence_score']:.3f}",
                    item["rationale"],
                    "YES" if item["is_manual_override"] else "NO",
                    "ACCEPTED" if item["is_accepted"] else "REJECTED"
                ])

            return output.getvalue()

    @classmethod
    def export_job_csv_file(cls, job_id: str, output_dir: Optional[Path] = None) -> Path:
        csv_content = cls.generate_job_csv(job_id)
        if not output_dir:
            from src.core.config import PROJECT_ROOT
            output_dir = PROJECT_ROOT / "logs"
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_file = output_dir / f"audit_job_{job_id}.csv"
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            f.write(csv_content)
        return csv_file

    @staticmethod
    def generate_csv_report(history_id: int) -> str:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_history WHERE id = ?", (history_id,))
            row = cur.fetchone()
            if not row:
                return ""

            job_id = row["job_id"]
            return ExportService.generate_job_csv(job_id)
