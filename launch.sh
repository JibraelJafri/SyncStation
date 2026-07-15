#!/usr/bin/env bash
set -e

# Change directory to script location
cd "$(dirname "$0")"

echo "========================================================"
echo " SyncStation: Spotify -> YouTube Music Sync Manager"
echo "========================================================"
echo ""

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 app.py "$@"
