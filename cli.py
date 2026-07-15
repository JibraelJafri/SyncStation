#!/usr/bin/env python3
"""
SyncStation CLI Entry Point
Run:
    python cli.py            # Launch interactive TUI manager
    python cli.py list       # List playlists in export
    python cli.py diff       # Inspect diffs & dry run
    python cli.py sync       # Sync playlists (e.g. --all or --name "Rock")
    python cli.py mirrors    # View mirrored playlists & sync status
    python cli.py auth       # Test/setup authentication
    python cli.py resume     # Resume interrupted transfers
"""
import sys
from pathlib import Path

# Configure UTF-8 stream encoding for cross-platform safety
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from src.cli.main import main

if __name__ == "__main__":
    main()
