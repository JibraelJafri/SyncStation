import logging
import re
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import List, Optional, Any

SECRET_PATTERNS = [
    re.compile(r"SAPISIDHASH\s+[^\s'\"]+", re.IGNORECASE),
    re.compile(r"SID=[^;\s'\"]+", re.IGNORECASE),
    re.compile(r"__Secure-[^=]+=[^;\s'\"]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[^\s'\"]+", re.IGNORECASE),
    re.compile(r"client_secret[\"':\s=]+[a-zA-Z0-9_-]+", re.IGNORECASE),
    re.compile(r"access_token[\"':\s=]+[a-zA-Z0-9_-]+", re.IGNORECASE),
    re.compile(r"arl=[\"']?[a-zA-Z0-9_-]+[\"']?", re.IGNORECASE),
]

def sanitize_text(text: str) -> str:
    sanitized = str(text)
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    return sanitized

class SafeFormatter(logging.Formatter):
    def format(self, record):
        orig_msg = super().format(record)
        return sanitize_text(orig_msg)

def _get_log_dir() -> Path:
    try:
        from src.core.config import PROJECT_ROOT
        log_dir = PROJECT_ROOT / "logs"
    except Exception:
        log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

def setup_logger(name: str = "syncstation") -> logging.Logger:
    app_logger = logging.getLogger(name)
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        try:
            log_dir = _get_log_dir()
            fh = RotatingFileHandler(
                log_dir / "syncstation.log",
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5,
                encoding="utf-8"
            )
            fh.setLevel(logging.INFO)
            fh.setFormatter(SafeFormatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            app_logger.addHandler(fh)
        except Exception:
            pass

        # Console handler: only show warnings and errors to keep progress bars undisturbed
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(SafeFormatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        app_logger.addHandler(ch)

    return app_logger

def setup_match_logger(name: str = "syncstation.matcher") -> logging.Logger:
    m_logger = logging.getLogger(name)
    m_logger.setLevel(logging.INFO)
    m_logger.propagate = False  # Keep match telemetry isolated to matching.log
    if not m_logger.handlers:
        try:
            log_dir = _get_log_dir()
            fh = RotatingFileHandler(
                log_dir / "matching.log",
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8"
            )
            fh.setLevel(logging.INFO)
            fh.setFormatter(SafeFormatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            m_logger.addHandler(fh)
        except Exception:
            pass

    return m_logger

logger = setup_logger()
match_logger = setup_match_logger()

def log_match_telemetry(
    source_track: Any,
    destination_name: str,
    query: str,
    result: Any,
    candidates_count: int = 0,
    is_cached: bool = False,
    is_override: bool = False
):
    """Write structured telemetry to matching.log for high-fidelity auditability."""
    try:
        src_info = f'"{source_track.name}" - {source_track.artist} ({int(source_track.duration_seconds)}s)'
        if getattr(source_track, "isrc", None):
            src_info += f' [ISRC:{source_track.isrc}]'

        if is_override:
            match_logger.info(
                f"[OVERRIDE] {src_info} -> Matched: [{result.matched_track.video_id}] \"{result.matched_track.title}\" - {result.matched_track.artist} (Manual Override)"
            )
            return

        if is_cached:
            match_logger.info(
                f"[CACHE] {src_info} -> Matched: [{result.matched_track.video_id}] \"{result.matched_track.title}\" - {result.matched_track.artist} (Score: {result.confidence_score:.2f}, {result.confidence_tier.value})"
            )
            return

        if not result.matched_track:
            match_logger.warning(
                f"[NO_MATCH] {src_info} | Dest: {destination_name} | Query: '{query}' | Candidates: {candidates_count} | Rationale: {result.rationale}"
            )
            return

        tier = result.confidence_tier.value
        score = result.confidence_score
        cand = result.matched_track
        cand_info = f'[{cand.video_id}] "{cand.title}" - {cand.artist} ({int(cand.duration_seconds)}s)'

        tag = "MATCH"
        if tier == "AMBIGUOUS":
            tag = "AMBIGUOUS"
        elif not result.is_accepted:
            tag = "UNACCEPTED"

        alt_ids = [c.video_id for c in (result.alternative_candidates or [])[:3]]
        alt_str = f" | Alts: {alt_ids}" if alt_ids else ""

        match_logger.info(
            f"[{tag}] [{tier}] Score: {score:.3f} | {src_info} -> {cand_info} | {result.rationale}{alt_str}"
        )
    except Exception as e:
        logger.debug(f"Failed to log match telemetry: {e}")

def get_log_tail(log_type: str = "matching", lines: int = 50) -> List[str]:
    """Safely tail the most recent N lines of a log file."""
    log_dir = _get_log_dir()
    target_file = log_dir / ("matching.log" if log_type == "matching" else "syncstation.log")
    if not target_file.exists():
        return []

    try:
        with open(target_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            return [line.rstrip() for line in all_lines[-lines:]]
    except Exception as e:
        return [f"Error reading log file: {e}"]
