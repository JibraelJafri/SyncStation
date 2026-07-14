import json
import csv
import zipfile
import io
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

from src.core.models import Playlist, Track, SpotifySnapshotMetadata
from src.core.config import PROJECT_ROOT
from src.providers.base import MusicSource
from src.core.logger import logger
from src.core.database import DatabaseManager

class SpotifyExportSource(MusicSource):
    """
    Production-grade Spotify Export Parser.
    Supports:
    - Official Spotify Account Data dumps (JSON)
    - Spotify Data export ZIP archives containing Playlist/YourLibrary JSONs
    - Spicetify JSON exports
    - CSV exports (Exportify, Spotlistr, Soundiiz, TuneMyMusic)
    - Single playlist JSON files
    - Graceful fallback for delisted tracks, missing metadata, and unicode edge cases.
    """

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = Path(file_path) if file_path else self._find_best_export_file()
        self._data: Optional[Dict[str, Any]] = None
        self._metadata: Optional[SpotifySnapshotMetadata] = None
        self._cached_playlists: Optional[List[Playlist]] = None
        if self.file_path and self.file_path.exists():
            self._load_data()

    def _find_best_export_file(self) -> Optional[Path]:
        # 1. Check active snapshot in DB if it exists on disk
        snapshots = DatabaseManager.list_snapshots_meta()
        for s in snapshots:
            if s.get("is_active"):
                p = Path(s["file_path"])
                if p.exists():
                    return p

        # 2. Check PROJECT_ROOT, exports/, and data/ for export files
        search_dirs = [PROJECT_ROOT, PROJECT_ROOT / "exports", PROJECT_ROOT / "data"]
        candidates = []
        for d in search_dirs:
            if d.exists():
                candidates.extend(list(d.glob("*.json")) + list(d.glob("*.csv")) + list(d.glob("*.zip")))

        candidates = [
            c for c in candidates
            if c.name not in ["sync_cache.json", "package.json", "tsconfig.json", "settings.json"]
            and not c.name.startswith(".")
        ]

        if not candidates:
            return None

        # Prefer files with "spotify-export" or "export" or "playlist" in name
        export_named = [c for c in candidates if any(k in c.name.lower() for k in ["spotify-export", "export", "playlist", "yourlibrary"])]
        if export_named:
            export_named.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return export_named[0]

        candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return candidates[0]

    def _load_data(self):
        self._cached_playlists = None
        if not self.file_path or not self.file_path.exists():
            return
        try:
            suffix = self.file_path.suffix.lower()
            if suffix == ".json":
                with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                    self._data = self._normalize_json_data(json.load(f))
            elif suffix == ".csv":
                with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    self._data = {"csv_items": list(reader)}
            elif suffix == ".zip":
                self._load_from_zip(self.file_path)

            self._extract_and_save_metadata()
        except Exception as e:
            logger.error(f"Error loading export snapshot {self.file_path}: {e}")
            self._data = None

    def _load_from_zip(self, zip_path: Path):
        """Extract and aggregate playlists from a Spotify ZIP download."""
        aggregated_playlists = []
        with zipfile.ZipFile(zip_path, "r") as z:
            for filename in z.namelist():
                if filename.lower().endswith(".json"):
                    try:
                        with z.open(filename) as f:
                            raw = json.load(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
                            norm = self._normalize_json_data(raw)
                            if "playlists" in norm:
                                aggregated_playlists.extend(norm["playlists"])
                    except Exception as e:
                        logger.debug(f"Could not parse {filename} in zip: {e}")

        if aggregated_playlists:
            self._data = {"playlists": aggregated_playlists}
        else:
            self._data = None

    def _normalize_json_data(self, raw_json: Any) -> Dict[str, Any]:
        """Normalize varied Spotify JSON export schemas into a consistent internal dictionary."""
        if isinstance(raw_json, dict):
            # Standard Spotify Privacy Data Export: {"playlists": [...]}
            if "playlists" in raw_json:
                return raw_json
            # YourLibrary.json format: {"tracks": [...], "albums": [...], "other": [...]}
            if "tracks" in raw_json and isinstance(raw_json["tracks"], list):
                # Could be a single playlist or liked songs
                return {
                    "playlists": [
                        {
                            "name": raw_json.get("name", "Saved Library Tracks"),
                            "items": [{"track": t} for t in raw_json["tracks"]]
                        }
                    ]
                }
            # Single playlist export: {"name": "...", "items": [...]}
            if "name" in raw_json and ("items" in raw_json or "tracks" in raw_json):
                return {"playlists": [raw_json]}
            return raw_json

        elif isinstance(raw_json, list):
            # List of playlists: [{"name": "...", "items": [...]}, ...]
            if raw_json and isinstance(raw_json[0], dict) and ("items" in raw_json[0] or "tracks" in raw_json[0]):
                return {"playlists": raw_json}
            # List of tracks: [{"trackName": ...}, ...]
            return {
                "playlists": [
                    {
                        "name": "Exported Tracks",
                        "items": raw_json
                    }
                ]
            }

        return {}

    def _extract_and_save_metadata(self):
        if not self.file_path or not self._data:
            return

        stat = self.file_path.stat()
        file_size_kb = round(stat.st_size / 1024.0, 1)
        modified_at = datetime.fromtimestamp(stat.st_mtime)

        playlist_count = 0
        total_tracks = 0
        user_id = None
        export_date = None

        if isinstance(self._data, dict):
            if "playlists" in self._data:
                playlist_count = len(self._data["playlists"])
                for pl in self._data["playlists"]:
                    items = pl.get("items") or pl.get("tracks") or []
                    total_tracks += len(items)
            elif "csv_items" in self._data:
                playlist_count = 1
                total_tracks = len(self._data["csv_items"])

            user_id = self._data.get("userId") or self._data.get("user")
            export_date = self._data.get("exportDate") or self._data.get("date")

        meta = {
            "file_name": self.file_path.name,
            "file_path": str(self.file_path.resolve()),
            "file_size_kb": file_size_kb,
            "modified_at": modified_at.isoformat(),
            "export_date": export_date,
            "playlist_count": playlist_count,
            "total_tracks": total_tracks,
            "user_id": user_id,
            "is_active": True
        }
        try:
            p_str = str(self.file_path.resolve()).lower()
            if "temp" not in p_str and "pytest" not in p_str:
                DatabaseManager.save_snapshot_meta(meta)
        except Exception as e:
            logger.debug(f"Could not persist snapshot metadata: {e}")
        self._metadata = SpotifySnapshotMetadata(**meta)

    def get_metadata(self) -> Optional[SpotifySnapshotMetadata]:
        return self._metadata

    def get_playlists(self) -> List[Playlist]:
        if self._cached_playlists is not None:
            return self._cached_playlists

        if not self._data:
            return []

        playlists = []
        if isinstance(self._data, dict) and "playlists" in self._data:
            for pl in self._data["playlists"]:
                items = pl.get("items") or pl.get("tracks") or []
                p_name = pl.get("name", "Untitled Playlist").strip()
                p_uri = pl.get("uri") or f"spotify:playlist:{p_name.replace(' ', '_')}"
                p_id = p_uri.replace("spotify:playlist:", "")
                playlists.append(Playlist(
                    id=p_id,
                    uri=p_uri,
                    name=p_name,
                    description=pl.get("description", ""),
                    track_count=len(items),
                    source_type="export",
                    snapshot_id=self.file_path.name if self.file_path else None
                ))
        elif isinstance(self._data, dict) and "csv_items" in self._data:
            name = self.file_path.stem if self.file_path else "CSV Playlist"
            playlists.append(Playlist(
                id=name,
                uri=f"spotify:playlist:{name}",
                name=name,
                description="Imported from CSV file",
                track_count=len(self._data["csv_items"]),
                source_type="export",
                snapshot_id=self.file_path.name if self.file_path else None
            ))

        self._cached_playlists = playlists
        return playlists

    def get_playlist_tracks(self, playlist_id_or_name: str) -> Playlist:
        if not self._data:
            return Playlist(name=playlist_id_or_name, tracks=[], source_type="export")

        raw_items = []
        target_name = playlist_id_or_name
        target_desc = ""
        target_uri = ""

        if isinstance(self._data, dict) and "playlists" in self._data:
            req_clean = playlist_id_or_name.lower().strip()
            for pl in self._data["playlists"]:
                p_raw = pl.get("name", "")
                p_clean = p_raw.lower().strip()
                p_uri = (pl.get("uri") or "").lower()
                name_match = p_clean == req_clean or p_clean.replace(" ", "_") == req_clean.replace(" ", "_")
                uri_match = p_uri == req_clean or req_clean in p_uri
                if name_match or uri_match:
                    raw_items = pl.get("items") or pl.get("tracks") or []
                    target_name = p_raw
                    target_desc = pl.get("description", "")
                    target_uri = pl.get("uri", "")
                    break

            if not raw_items and len(self._data["playlists"]) == 1:
                pl = self._data["playlists"][0]
                raw_items = pl.get("items") or pl.get("tracks") or []
                target_name = pl.get("name", target_name)
                target_desc = pl.get("description", "")
                target_uri = pl.get("uri", "")

        elif isinstance(self._data, dict) and "csv_items" in self._data:
            raw_items = self._data["csv_items"]

        tracks = []
        for it in raw_items:
            t = it.get("track") or it.get("localTrack") or it.get("episode") or it

            name = (
                t.get("trackName")
                or t.get("name")
                or t.get("title")
                or t.get("Track Name")
                or t.get("Track name")
                or t.get("Title")
                or t.get("episodeName")
                or ""
            )
            artist = (
                t.get("artistName")
                or t.get("artist")
                or t.get("Artist Name(s)")
                or t.get("Artist name")
                or t.get("Artist")
                or t.get("showName")
                or ""
            )
            album = (
                t.get("albumName")
                or t.get("album")
                or t.get("Album Name")
                or t.get("Album name")
                or t.get("Album")
                or ""
            )
            uri = (
                t.get("trackUri")
                or t.get("uri")
                or t.get("Spotify ID")
                or t.get("Track URI")
                or t.get("URI")
                or ""
            )
            isrc = t.get("isrc") or t.get("ISRC") or None
            
            # Duration parsing (ms or seconds or MM:SS)
            dur_raw = (
                t.get("durationMs")
                or t.get("duration_ms")
                or t.get("Duration (ms)")
                or t.get("duration")
                or t.get("Duration")
                or 0
            )
            duration_seconds = 0.0
            if isinstance(dur_raw, (int, float)):
                duration_seconds = float(dur_raw) / 1000.0 if dur_raw > 1000 else float(dur_raw)
            elif isinstance(dur_raw, str) and ":" in dur_raw:
                parts = dur_raw.split(":")
                try:
                    if len(parts) == 2:
                        duration_seconds = float(int(parts[0]) * 60 + int(parts[1]))
                    elif len(parts) == 3:
                        duration_seconds = float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
                except Exception:
                    duration_seconds = 0.0

            # Delisted track fallback (sometimes Spotify leaves trackName empty but keeps albumName)
            if not name and album:
                name = album

            if isinstance(artist, list):
                artist = ", ".join([a.get("name", str(a)) if isinstance(a, dict) else str(a) for a in artist])

            if name:
                t_id = uri.replace("spotify:track:", "") if uri else ""
                tracks.append(Track(
                    id=t_id,
                    uri=uri or f"spotify:track:{t_id}",
                    name=str(name).strip(),
                    artist=str(artist).strip() if artist != "Various Artists" else "",
                    album=str(album).strip(),
                    duration_seconds=duration_seconds,
                    isrc=isrc
                ))

        p_id = target_uri.replace("spotify:playlist:", "") if target_uri else target_name.replace(" ", "_")
        return Playlist(
            id=p_id,
            uri=target_uri or f"spotify:playlist:{p_id}",
            name=target_name,
            description=target_desc,
            track_count=len(tracks),
            tracks=tracks,
            source_type="export",
            snapshot_id=self.file_path.name if self.file_path else None
        )

    def get_liked_tracks(self) -> List[Track]:
        if not self._data or not isinstance(self._data, dict):
            return []

        lib_tracks = self._data.get("library", {}).get("tracks", []) or self._data.get("saved_tracks", [])
        tracks = []
        for it in lib_tracks:
            t = it.get("track", it)
            name = t.get("trackName") or t.get("name") or ""
            artist = t.get("artistName") or t.get("artist") or ""
            album = t.get("albumName") or t.get("album") or ""
            uri = t.get("trackUri") or t.get("uri") or ""
            if name:
                tracks.append(Track(
                    id=uri.replace("spotify:track:", ""),
                    uri=uri,
                    name=name,
                    artist=artist if artist != "Various Artists" else "",
                    album=album,
                    duration_seconds=0.0
                ))
        return tracks
