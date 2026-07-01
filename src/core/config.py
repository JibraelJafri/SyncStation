import os
import json
import configparser
from pathlib import Path
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATABASE_PATH = Path(os.environ.get("SYNC_DB_PATH", str(PROJECT_ROOT / "sync_app.db")))
STATIC_DIR = PROJECT_ROOT / "src" / "static"
CACHE_FILE = PROJECT_ROOT / "sync_cache.json"

LOCAL_APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
SETTINGS_INI_PATH = LOCAL_APP_DIR / "SyncStation" / "settings.ini"
LEGACY_SETTINGS_PATH = LOCAL_APP_DIR / "spotify_to_ytmusic" / "Cache" / "settings.ini"

# Use SyncStation path, but check legacy path if legacy settings exist
if not SETTINGS_INI_PATH.exists() and LEGACY_SETTINGS_PATH.exists():
    SETTINGS_INI_PATH = LEGACY_SETTINGS_PATH

class AppConfig:
    @classmethod
    def get_settings(cls) -> Dict[str, Any]:
        result = {
            "youtube": {
                "configured": False,
                "auth_type": "browser",
                "headers": None,
                "client_id": None
            },
            "spotify": {
                "configured": False,
                "client_id": None,
                "client_secret": None,
                "use_oauth": False
            }
        }
        
        if SETTINGS_INI_PATH.exists():
            try:
                cp = configparser.ConfigParser(interpolation=None)
                cp.read(SETTINGS_INI_PATH, encoding="utf-8")
                
                if "youtube" in cp:
                    yt = cp["youtube"]
                    headers_str = yt.get("headers", "")
                    result["youtube"]["configured"] = bool(headers_str and headers_str.startswith("{"))
                    result["youtube"]["auth_type"] = yt.get("auth_type", "browser")
                    result["youtube"]["headers"] = headers_str
                    result["youtube"]["client_id"] = yt.get("client_id", "")
                    
                if "spotify" in cp:
                    sp = cp["spotify"]
                    c_id = sp.get("client_id", "")
                    result["spotify"]["configured"] = bool(c_id and c_id != "id_from_developer_console")
                    result["spotify"]["client_id"] = c_id
                    result["spotify"]["client_secret"] = sp.get("client_secret", "")
                    result["spotify"]["use_oauth"] = sp.getboolean("use_oauth", False)
            except Exception:
                pass
                
        return result

    @classmethod
    def save_youtube_headers(cls, headers_json_str: str, auth_type: str = "browser") -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "youtube" not in cp:
            cp["youtube"] = {}
        cp["youtube"]["headers"] = headers_json_str
        cp["youtube"]["auth_type"] = auth_type
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)

    @classmethod
    def save_spotify_credentials(cls, client_id: str, client_secret: str, use_oauth: bool = False) -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "spotify" not in cp:
            cp["spotify"] = {}
        cp["spotify"]["client_id"] = client_id.strip()
        cp["spotify"]["client_secret"] = client_secret.strip()
        cp["spotify"]["use_oauth"] = str(use_oauth)
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)
