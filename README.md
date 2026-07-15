# ⚡ SyncStation — Spotify → YouTube Music Migration & Sync Engine

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/Tests-41%20Passing-brightgreen.svg)]()
[![TUI](https://img.shields.io/badge/UI-Rich%20%2B%20InquirerPy-cyan.svg)]()
[![Zero Duplicates](https://img.shields.io/badge/Duplicates-0%20Guaranteed-orange.svg)]()
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)]()

**SyncStation** is a production-grade, terminal-first playlist migration and synchronization application engineered to transfer playlists from Spotify to YouTube Music with **zero duplicate insertions**, **multi-factor confidence matching**, and **tactile arrow-key TUI controls**.

> *"Wake up, Samurai. Your music belongs to you, not the walled gardens of streaming corpos."*

Because Spotify's Web API restricts third-party developer playlist modifications, SyncStation operates directly on **Spotify's exported data** (Spicetify Data Porter, Account Data JSON dumps, ZIP downloads, and CSV exports), allowing you to migrate thousands of songs reliably in seconds.

---

## 🌟 Key Capabilities

### 1. 🎮 World-Class Terminal User Interface (TUI)
- **Arrow-Key Navigation (`↑` / `↓`)**: Fluid keyboard controls for menus and actions—no manual number typing required.
- **Interactive Checklists with Fuzzy Search**: Press `Space` to toggle playlists, `a` to select all, `i` to invert selection, or simply start typing (`"cyber"`) to auto-filter through 50+ playlists instantly.
- **Structured Diff Cards**: Inspect exactly what will be added, restored, or preserved on YouTube Music before making any changes.
- **Live Progress Dashboards**: Smooth gradient progress bars powered by `rich` with animated spinners (`⠋`), elapsed/ETA timers, and live track labels.

### 2. ⚡ Intelligent Delta Sync (0 Duplicates Guarantee)
- When you update your Spotify export next month with +15 new songs, SyncStation **does not** create redundant playlists like `My Playlist (2)`.
- It analyzes your existing YouTube Music playlist, computes the exact delta, matches only the missing tracks, and appends them with **zero duplicate insertions**.

### 3. 🛡️ Accidental Deletion Recovery
- If tracks were accidentally removed from your YouTube Music playlist, SyncStation compares the live playlist against its tracked SQLite manifest, detects the missing tracks (`↺ [RESTORE]`), and restores them to YouTube Music using known IDs without re-matching.

### 4. 🧠 Multi-Factor Confidence-Weighted Matcher
- **Edition & Remaster Stripping**: Sanitizes noise like `(Remastered 2011)`, `[Deluxe Edition]`, `(From "Motion Picture")`, `(Lyric Video)`, `(Visualizer)`.
- **Version Mismatch Filters**: Penalizes and prevents unwanted `[Live]`, `(Remix)`, `(Acoustic)`, or `(Instrumental)` substitutes when the original album cut was requested.
- **Duration Penalty Curve**: Non-linear tolerance curves ensure studio versions aren't replaced by extended 10-minute live sets.
- **Match Cache**: Verified song resolutions are stored locally in SQLite for instant repeat syncs.

### 5. 🪞 YouTube-Only Track Preservation
- Any tracks you added directly on YouTube Music are recognized and preserved intact under the default `NEVER_REMOVE` policy.

---

## 🚀 Quick Start

### 1. Requirements & Setup
Ensure you have **Python 3.10+** installed.

```bash
# Clone or navigate to the repository
cd SyncStation

# Install dependencies into your virtual environment
pip install -r requirements.txt
```

### 2. Launch the Application

```bash
# On Windows (One-Click Launcher):
launch.bat

# On Linux / macOS:
./launch.sh

# Or direct Python command:
python app.py
# (or)
python cli.py
```

---

## 💻 CLI Subcommands

SyncStation supports both interactive TUI mode and scriptable direct subcommands:

```bash
python cli.py                         # Launch interactive TUI manager
python cli.py list                    # List all playlists found in the Spotify export
python cli.py diff                    # Inspect live diffs & dry run against YouTube Music
python cli.py diff --name "mIcHaEl?"  # Inspect diff for a specific playlist
python cli.py sync --all              # Fast batch delta sync across all playlists
python cli.py sync --name "Rock"      # Synchronize a specific playlist
python cli.py mirrors                 # View all active mirrored playlists and sync states
python cli.py auth                    # Check or configure YouTube Music session
python cli.py resume                  # Resume any interrupted sync operations
```

---

## 📂 Supported Spotify Export Formats

SyncStation automatically detects and normalizes:
- **Spicetify Data Porter (Recommended & Instant)**: One-click JSON exports directly from the Spotify Desktop app via Spicetify's **Data Porter** extension (`data-porter.js`)—zero waiting time.
- **Official Spotify Account Privacy Archives (JSON / ZIP)**: `spotify-export-*.json`, `YourLibrary.json`, `Playlist1.json`, or entire downloaded `.zip` packages from [Spotify Account Privacy](https://www.spotify.com/account/privacy/).
- **CSV Playlist Exports**: CSV dumps from Exportify, Spotlistr, Soundiiz, or TuneMyMusic (supports standard column headers: `Track Name`, `Artist Name(s)`, `Album`, `Duration (ms)`).

---

## 🔑 YouTube Music Authentication

Because YouTube Music does not provide public third-party OAuth for managing library playlists, SyncStation authenticates via secure session headers:

1. Open your browser and log into [music.youtube.com](https://music.youtube.com).
2. Press **F12** to open Developer Tools → switch to the **Network** tab.
3. Refresh the page, click any request (e.g. `browse` or `v1/`), and right-click → **Copy as cURL** (or copy the `cookie` header).
4. In SyncStation, select **YouTube Music Session & Diagnostics** (Option `6` / `python cli.py auth`) and paste the copied string.

> **🔒 Privacy & Security**: Your session headers are stored exclusively in your local user settings directory (`%LOCALAPPDATA%\SyncStation\settings.ini`). They are never uploaded to third-party servers and are automatically redacted from all application logs.

---

## 🏗️ Architecture & Codebase Map

```
SyncStation/
├── app.py                     # Primary entry point (UTF-8 stream safe)
├── cli.py                     # CLI script & subcommand dispatcher
├── launch.bat                 # Windows one-click launcher
├── launch.sh                  # Linux / macOS launcher
├── LICENSE                    # MIT License
├── requirements.txt           # Pure dependencies (rich, InquirerPy, ytmusicapi, pydantic, rapidfuzz)
├── sync_app.db                # SQLite database (mirrors, manifests, match cache, job checkpoints)
├── src/
│   ├── cli/
│   │   ├── main.py            # CLI controller, interactive flows (Wizard, Diff, Quick Sync)
│   │   └── tui.py             # Rich & InquirerPy TUI components, cards, tables, progress bars
│   ├── core/
│   │   ├── config.py          # Paths, settings.ini loader, and environment config
│   │   ├── database.py        # SQLite schema, migrations, and CRUD operations
│   │   ├── logger.py          # Safe redacted file logger (logs/syncstation.log)
│   │   └── models.py          # Pydantic v2 domain models (Playlist, Track, SyncPlan, Mirror)
│   ├── domain/
│   │   ├── discovery_engine.py# Overlap analyzer to match Spotify playlists with existing YT playlists
│   │   ├── matcher.py         # Multi-factor track scoring engine with version penalty filters
│   │   ├── normalizer.py      # Unicode normalizer, edition/remaster noise cleaner
│   │   ├── overrides.py       # User manual match overrides manager
│   │   └── sync_planner.py    # Delta sync planner, restoration detection, policy evaluator
│   ├── providers/
│   │   ├── base.py            # MusicSource and MusicDestination abstract interfaces
│   │   ├── spotify/           # SpotifyExportSource (JSON, ZIP, CSV), ApiSource, WebSource
│   │   └── youtube/           # YouTubeMusicDestination, session header parser
│   └── services/
│       ├── mirror_service.py  # Playlist mirror linking and delta tracking service
│       ├── review_service.py  # Job inspection and CSV audit export service
│       └── sync_service.py    # High-level transfer and delta execution engine
└── tests/                     # Comprehensive test suite (41 unit & integration tests)
    ├── test_cli_commands.py   # CLI subcommand tests (list, diff, sync, auth, mirrors)
    ├── test_db_export.py      # Export parsing and DB overrides tests
    ├── test_discovery.py      # Mirror discovery and overlap matching tests
    ├── test_edge_cases.py     # 404 auto-healing, duplicate tracks, UTF-8 safety tests
    ├── test_export_source.py  # JSON, CSV, ZIP, and delisted track parser tests
    ├── test_matcher.py        # Exact, remaster, live/remix penalty, duration tests
    ├── test_mirrored_playlists_db.py # Mirror and manifest CRUD tests
    ├── test_normalizer.py     # Unicode, noise stripping, and artist cleaning tests
    ├── test_sync_planner.py   # Sync planner additions, restorations, removals tests
    ├── test_sync_service.py   # Transfer execution and delta idempotency tests
    ├── test_tui.py            # Rich cards and tables rendering tests
    └── test_zip_and_cli.py    # ZIP archive extraction and noise flag tests
```

---

## 🧪 Testing & Verification

Run the test suite with `pytest`:

```bash
pytest -v
```

```text
============================= 41 passed in ~4.72s =============================
```

---

## 📄 License
MIT License. Free and open source.
