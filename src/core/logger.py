import logging
import re
import sys
from datetime import datetime, timezone

SECRET_PATTERNS = [
    re.compile(r"SAPISIDHASH\s+[^\s'\"]+", re.IGNORECASE),
    re.compile(r"SID=[^;\s'\"]+", re.IGNORECASE),
    re.compile(r"__Secure-[^=]+=[^;\s'\"]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[^\s'\"]+", re.IGNORECASE),
    re.compile(r"client_secret[\"':\s=]+[a-zA-Z0-9_-]+", re.IGNORECASE),
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

def setup_logger(name: str = "spotify_sync") -> logging.Logger:
    app_logger = logging.getLogger(name)
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        # File handler for complete background logs
        try:
            from src.core.config import PROJECT_ROOT
            log_dir = PROJECT_ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / "syncstation.log", encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(SafeFormatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            app_logger.addHandler(fh)
        except Exception:
            pass

        # Console handler: only show warnings and errors to avoid disrupting progress bars
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(SafeFormatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        app_logger.addHandler(ch)

    return app_logger

logger = setup_logger()
