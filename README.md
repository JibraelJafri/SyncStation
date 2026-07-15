# ⚡ SyncStation — Spotify → YouTube Music & Deezer Migration Engine

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/Tests-93%20Passing-brightgreen.svg)]()
[![TUI](https://img.shields.io/badge/UI-Rich%20%2B%20InquirerPy-cyan.svg)]()
[![Zero Duplicates](https://img.shields.io/badge/Duplicates-0%20Guaranteed-orange.svg)]()
[![Destinations](https://img.shields.io/badge/Destinations-YouTube%20Music%20%7C%20Deezer-magenta.svg)]()
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)]()

**SyncStation** is a production-grade, terminal-first playlist migration and synchronization application engineered to transfer playlists from Spotify to **YouTube Music** and **Deezer** with **zero duplicate insertions**, **true 1:1 mirror duplicate pruning**, **inline interactive discrepancy resolution**, **multi-factor confidence matching**, and tactile arrow-key TUI controls.

> *"Wake up, Samurai. Your music belongs to you, not the walled gardens of streaming corpos."*

Because Spotify restricts third-party developer playlist modifications, SyncStation operates directly on **Spotify's exported data** (Spicetify Data Porter, Account Data JSON dumps, ZIP downloads, and CSV exports), allowing you to migrate thousands of songs reliably in seconds.

---

## 🌟 Key Capabilities

### 1. 🎯 Multi-Platform Destination Engine (YouTube Music & Deezer)
- **Pluggable Destination Architecture**: Switch effortlessly between YouTube Music and Deezer via `--dest {youtube,deezer}` or live inside the interactive TUI.
- **ISRC Direct Lookup & Advanced Search**: Direct ISRC resolution (`/track/isrc:{isrc}`), field-level search (`artist:"..." track:"..."`), and fuzzy fallback.
- **Rate-Limiting & Quota Protection**: Built-in 120ms request pacing and exponential backoff on HTTP 429 / Deezer error code 4.
- **Flexible Deezer Authentication**: Connect via direct API token, browser OAuth 2.0 loopback server (`http://localhost:8080/callback`), or browser `arl` session cookies.

### 2. 🧠 Precision-Engineered Track Matcher
- **ISRC Exact Matching**: Instant 1.00 score bypass for identical recordings.
- **Token Ratio Damping**: Dynamic dampening prevents subset false positives (e.g., *"Run"* will never match *"Run For Your Life"*; *"Stay"* will never match *"Stay with Me"*).
- **Short Artist Boundary Protection**: Short artist names (≤ 4 chars like *"H.E.R."*, *"In"*, *"The"*) require strict boundary equality.
- **Dual Identity Gating**: If artist match score < 50%, or title score < 60%, the composite score is heavily gated downward.
- **Penalties & Mismatch Filters**: Penalizes and prevents unwanted `[Live]`, `(Remix)`, `(Cover)`, `(Parody)`, `(Sped Up)`, or `(Instrumental)` substitutes.

### 3. 🔍 In-App Match Audit & Quality Telemetry
- **Match Confidence Scorecards**: Full breakdown by confidence tier (💎 EXACT ≥ 92%, 🟢 HIGH 80-91%, 🟡 PROBABLE 65-79%, 🟠 AMBIGUOUS 45-64%, 🔴 NO MATCH < 45%).
- **Interactive Override Picker**: Review low-confidence matches and easily override them with alternative candidates or manual video/track IDs.
- **Audit Logging & CSV Exports**: Dual rotating log files (`logs/syncstation.log` and `logs/matching.log`) and one-click CSV export of match audit reports.

### 4. 🎮 World-Class Terminal User Interface (TUI)
- **Arrow-Key Navigation (`↑` / `↓`)**: Fluid keyboard controls for menus and actions.
- **Interactive Checklists with Fuzzy Search**: Press `Space` to toggle playlists, `a` to select all, `i` to invert selection, or type to filter instantly.
- **Structured Diff Cards**: Inspect exactly what will be added, restored, or preserved before making any changes.
- **Live Progress Dashboards**: Smooth gradient progress bars powered by `rich` with animated spinners (`⠋`), elapsed/ETA timers, and live track labels.

### 5. ⚡ Intelligent Delta Sync & Duplicate Pruning (0 Duplicates Guarantee)
- When you update your Spotify export next month with new songs, SyncStation **does not** create redundant playlists.
- It analyzes your destination playlist, computes the exact delta, matches only missing tracks, and appends them with **zero duplicates**.
- **Pruning Redundant Remote Tracks**: Automatically detects any existing duplicate tracks or deleted songs on the destination and safely removes them according to your `RemovalPolicy` (`ASK_BEFORE_REMOVE`, `MIRROR_REMOVALS`, `NEVER_REMOVE`).

### 6. 🛠️ Interactive Inline Discrepancy Resolver
- If any tracks are omitted or ambiguous (e.g., misspelled metadata or live acoustic editions), SyncStation offers an interactive review loop right inside the terminal.
- Browse top alternatives with similarity scores, run live manual destination searches, or paste IDs directly to push matches to the live playlist immediately and record manifest overrides!

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
python cli.py                                  # Launch interactive TUI manager
python cli.py list                             # List all playlists found in Spotify export
python cli.py diff --dest deezer               # Inspect dry run against Deezer
python cli.py diff --dest youtube              # Inspect dry run against YouTube Music
python cli.py sync --all --dest deezer         # Batch delta sync all playlists to Deezer
python cli.py sync --name "Rock" --dest youtube# Sync specific playlist to YouTube Music
python cli.py mirrors                          # View all active mirrored playlists and sync states
python cli.py auth                             # Check or configure service credentials
python cli.py audit                            # Inspect match scorecards and flagged tracks
python cli.py audit --flagged                  # Inspect only ambiguous / low-confidence tracks
python cli.py audit --export                   # Export match audit report to CSV
python cli.py logs --type matching             # Tail recent matching evaluation logs
python cli.py resume                           # Resume any interrupted sync operations
```

---

## 📂 Supported Spotify Export Formats

SyncStation automatically detects and normalizes:
- **Spicetify Data Porter (Recommended & Instant)**: One-click JSON exports directly from the Spotify Desktop app via Spicetify's **Data Porter** extension (`data-porter.js`)—zero waiting time.
- **Official Spotify Account Privacy Archives (JSON / ZIP)**: `spotify-export-*.json`, `YourLibrary.json`, `Playlist1.json`, or entire downloaded `.zip` packages from [Spotify Account Privacy](https://www.spotify.com/account/privacy/).
- **CSV Playlist Exports**: CSV dumps from Exportify, Spotlistr, Soundiiz, or TuneMyMusic.

---

## 🔑 Authentication

### YouTube Music
SyncStation authenticates via secure session headers:
1. Open your browser and log into [music.youtube.com](https://music.youtube.com).
2. Press **F12** → **Network** tab → Refresh → Right-click any request → **Copy as cURL**.
3. In SyncStation, select **Music Service Sessions & Authentication** (`python cli.py auth`) and paste the copied string.

### Deezer
SyncStation supports three authentication methods:
1. **Direct Access Token**: Paste your Deezer API access token.
2. **Browser OAuth 2.0**: Enter your Deezer App ID and Secret; SyncStation starts a local loopback server (`http://localhost:8080/callback`) and opens your browser for one-click authorization.
3. **ARL Cookie**: Paste your browser `arl` cookie from [deezer.com](https://www.deezer.com) (Storage / Cookies / `arl`).

---

## 🧪 Testing & Verification

Run the full test suite with `pytest`:

```bash
pytest -v
```

```text
============================= 93 passed in ~22s =============================
```

---

## 📄 License
MIT License. Free and open source.
