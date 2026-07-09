import csv
import json
import io
from typing import List, Dict, Any, Optional
from src.core.database import get_db, DatabaseManager
from src.core.models import CandidateTrack, Track
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.domain.overrides import OverrideManager

class ReviewService:
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

            # Get total count
            count_cur = conn.cursor()
            count_cur.execute(f"SELECT COUNT(*) FROM ({query})", params)
            total = count_cur.fetchone()[0]

            # Pagination
            query += " ORDER BY track_index ASC LIMIT ? OFFSET ?"
            params.extend([page_size, (page - 1) * page_size])

            cur.execute(query, params)
            items = [dict(row) for row in cur.fetchall()]

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
                is_accepted = 1
            WHERE id = ?
            """, (video_id, title, artist, item_id))

    @staticmethod
    def toggle_item_accepted(item_id: int, accepted: bool):
        with get_db() as conn:
            conn.execute("UPDATE sync_items SET is_accepted = ? WHERE id = ?", (int(accepted), item_id))

class ExportService:
    @staticmethod
    def generate_csv_report(history_id: int) -> str:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sync_history WHERE id = ?", (history_id,))
            row = cur.fetchone()
            if not row:
                return ""

            report = json.loads(row["report_json"])
            job_id = row["job_id"]

            cur.execute("SELECT * FROM sync_items WHERE job_id = ? ORDER BY track_index ASC", (job_id,))
            items = cur.fetchall()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["#", "Spotify Title", "Spotify Artist", "Spotify Album", "YT Match Title", "YT Match Artist", "YT Video ID", "Confidence", "Score", "Rationale", "Status"])

            for item in items:
                writer.writerow([
                    item["track_index"],
                    item["source_name"],
                    item["source_artist"],
                    item["source_album"],
                    item["matched_title"] or "",
                    item["matched_artist"] or "",
                    item["matched_video_id"] or "",
                    item["confidence_tier"],
                    item["confidence_score"],
                    item["rationale"],
                    "Accepted" if item["is_accepted"] else "Skipped"
                ])

            return output.getvalue()
