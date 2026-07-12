import pytest
from pathlib import Path
from unittest.mock import patch
from src.core.models import Track, CandidateTrack, MatchResult, ConfidenceTier
from src.core.database import init_db, DatabaseManager, get_db
from src.core.logger import log_match_telemetry, get_log_tail, match_logger
from src.services.review_service import ReviewService, ExportService
from src.cli.main import main

def test_log_match_telemetry_writes_to_log(tmp_path):
    """Verify log_match_telemetry formats and records match events."""
    source = Track(name="Test Track", artist="Test Artist", duration_seconds=200.0, isrc="US1234567890")
    candidate = CandidateTrack(
        video_id="vid_123",
        title="Test Track",
        artist="Test Artist",
        duration_seconds=200.0,
        result_type="song"
    )
    res = MatchResult(
        source_track=source,
        matched_track=candidate,
        confidence_score=0.98,
        confidence_tier=ConfidenceTier.EXACT,
        rationale="Title: 100% | Artist: 100% | Song (diff=0s)",
        alternative_candidates=[],
        is_accepted=True
    )
    log_match_telemetry(
        source_track=source,
        destination_name="YouTubeMusicDestination",
        query="Test Artist Test Track",
        result=res
    )
    lines = get_log_tail("matching", lines=10)
    assert any("Test Track" in line for line in lines)
    assert any("EXACT" in line for line in lines)

def test_audit_summary_and_items_flow(tmp_path):
    """Verify ReviewService properly calculates scorecards and retrieves items."""
    init_db()
    job_id = "test_audit_job"
    DatabaseManager.create_job(job_id, "Audit Test Playlist", "spotify:pl:1", "TRANSFER", 2)

    with get_db() as conn:
        conn.execute("""
        INSERT INTO sync_items (
            job_id, track_index, source_name, source_artist, source_album, source_duration,
            source_uri, matched_video_id, matched_title, matched_artist, confidence_score,
            confidence_tier, rationale, status, is_manual_override, is_accepted, alternatives_json
        ) VALUES
        (?, 1, 'Song One', 'Artist One', 'Album', 180.0, 'uri:1', 'vid1', 'Song One', 'Artist One', 0.95, 'EXACT', 'Title: 100%', 'ANALYZED', 0, 1, '[]'),
        (?, 2, 'Song Two', 'Artist Two', 'Album', 210.0, 'uri:2', 'vid2', 'Song Two (Remix)', 'Artist Two', 0.55, 'AMBIGUOUS', 'Penalty: Unwanted Remix', 'ANALYZED', 0, 0, '[{"video_id": "vid2_alt", "title": "Song Two Original", "artist": "Artist Two", "duration_seconds": 210.0, "result_type": "song"}]')
        """, (job_id, job_id))

    summary = ReviewService.get_audit_summary(job_id)
    assert summary["total"] == 2
    assert summary["exact"] == 1
    assert summary["ambiguous"] == 1
    assert summary["accepted"] == 1
    assert summary["unaccepted"] == 1

    # Verify flagged filtering
    flagged = ReviewService.get_audit_items(job_id, only_flagged=True)
    assert len(flagged) == 1
    assert flagged[0]["source_name"] == "Song Two"
    assert len(flagged[0]["alternatives"]) == 1
    assert flagged[0]["alternatives"][0]["video_id"] == "vid2_alt"

    # Test manual override on flagged item
    item_id = flagged[0]["id"]
    ReviewService.set_manual_override(item_id, "vid2_alt", "Song Two Original", "Artist Two")
    updated_summary = ReviewService.get_audit_summary(job_id)
    assert updated_summary["overrides"] == 1
    assert updated_summary["exact"] == 2

def test_export_service_job_csv(tmp_path):
    """Verify ExportService generates valid CSV reports."""
    init_db()
    job_id = "test_export_job"
    DatabaseManager.create_job(job_id, "Export Playlist", "spotify:pl:2", "TRANSFER", 1)

    with get_db() as conn:
        conn.execute("""
        INSERT INTO sync_items (
            job_id, track_index, source_name, source_artist, source_album, source_duration,
            source_uri, matched_video_id, matched_title, matched_artist, confidence_score,
            confidence_tier, rationale, status, is_manual_override, is_accepted, alternatives_json
        ) VALUES
        (?, 1, 'Sample Song', 'Sample Artist', 'Sample Album', 240.0, 'uri:s', 'vids', 'Sample Song', 'Sample Artist', 0.99, 'EXACT', 'Title: 100%', 'ANALYZED', 0, 1, '[]')
        """, (job_id,))

    csv_content = ExportService.generate_job_csv(job_id)
    assert "Sample Song" in csv_content
    assert "Sample Artist" in csv_content
    assert "EXACT" in csv_content

    # Export to file
    csv_file = ExportService.export_job_csv_file(job_id, output_dir=tmp_path)
    assert csv_file.exists()
    assert "Sample Song" in csv_file.read_text(encoding="utf-8")

def test_cli_audit_command(capsys):
    """Verify 'python cli.py audit' subcommand runs and displays scorecard."""
    init_db()
    job_id = "cli_audit_job"
    DatabaseManager.create_job(job_id, "CLI Audit Test", "spotify:pl:cli", "TRANSFER", 1)

    with get_db() as conn:
        conn.execute("""
        INSERT INTO sync_items (
            job_id, track_index, source_name, source_artist, source_album, source_duration,
            source_uri, matched_video_id, matched_title, matched_artist, confidence_score,
            confidence_tier, rationale, status, is_manual_override, is_accepted, alternatives_json
        ) VALUES
        (?, 1, 'Audit CLI Song', 'Artist', 'Album', 180.0, 'uri:c', 'vidc', 'Audit CLI Song', 'Artist', 1.0, 'EXACT', 'Title: 100%', 'ANALYZED', 0, 1, '[]')
        """, (job_id,))

    with patch("sys.argv", ["cli.py", "audit", "--job", job_id]):
        main()

    captured = capsys.readouterr()
    assert "Confidence Tier" in captured.out
    assert "EXACT" in captured.out
    assert "Total Evaluated" in captured.out

def test_cli_logs_command(capsys):
    """Verify 'python cli.py logs' subcommand runs and outputs log tail."""
    with patch("sys.argv", ["cli.py", "logs", "--lines", "5"]):
        main()

    captured = capsys.readouterr()
    assert "Recent MATCHING Log Entries" in captured.out
