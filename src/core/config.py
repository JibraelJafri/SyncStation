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
                "client_id": None,
                "proxy": ""
            },
            "spotify": {
                "configured": False,
                "client_id": None,
                "client_secret": None,
                "use_oauth": False
            },
            "deezer": {
                "configured": False,
                "access_token": "",
                "app_id": "",
                "app_secret": "",
                "arl": "",
                "auth_type": "token",
                "user_id": "",
                "user_name": "",
                "proxy": ""
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
                    result["youtube"]["proxy"] = yt.get("proxy", "")
                    
                if "spotify" in cp:
                    sp = cp["spotify"]
                    c_id = sp.get("client_id", "")
                    result["spotify"]["configured"] = bool(c_id and c_id != "id_from_developer_console")
                    result["spotify"]["client_id"] = c_id
                    result["spotify"]["client_secret"] = sp.get("client_secret", "")
                    result["spotify"]["use_oauth"] = sp.getboolean("use_oauth", False)

                if "deezer" in cp:
                    dz = cp["deezer"]
                    token = dz.get("access_token", "")
                    arl = dz.get("arl", "")
                    result["deezer"]["configured"] = bool(token or arl)
                    result["deezer"]["access_token"] = token
                    result["deezer"]["app_id"] = dz.get("app_id", "")
                    result["deezer"]["app_secret"] = dz.get("app_secret", "")
                    result["deezer"]["arl"] = arl
                    result["deezer"]["auth_type"] = dz.get("auth_type", "token")
                    result["deezer"]["user_id"] = dz.get("user_id", "")
                    result["deezer"]["user_name"] = dz.get("user_name", "")
                    result["deezer"]["proxy"] = dz.get("proxy", "")
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

    @classmethod
    def save_deezer_token(cls, access_token: str, user_id: str = "", user_name: str = "", auth_type: str = "token") -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "deezer" not in cp:
            cp["deezer"] = {}
        cp["deezer"]["access_token"] = access_token.strip()
        cp["deezer"]["auth_type"] = auth_type
        if user_id:
            cp["deezer"]["user_id"] = str(user_id)
        if user_name:
            cp["deezer"]["user_name"] = str(user_name)
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)

    @classmethod
    def save_deezer_arl(cls, arl: str) -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "deezer" not in cp:
            cp["deezer"] = {}
        cp["deezer"]["arl"] = arl.strip()
        cp["deezer"]["auth_type"] = "arl"
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)

    @classmethod
    def save_deezer_app_credentials(cls, app_id: str, app_secret: str) -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "deezer" not in cp:
            cp["deezer"] = {}
        cp["deezer"]["app_id"] = app_id.strip()
        cp["deezer"]["app_secret"] = app_secret.strip()
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)

    @classmethod
    def save_deezer_proxy(cls, proxy: str) -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "deezer" not in cp:
            cp["deezer"] = {}
        clean_proxy = proxy.strip()
        if clean_proxy:
            cp["deezer"]["proxy"] = clean_proxy
        elif "proxy" in cp["deezer"]:
            del cp["deezer"]["proxy"]
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)

    @classmethod
    def save_youtube_proxy(cls, proxy: str) -> None:
        SETTINGS_INI_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser(interpolation=None)
        if SETTINGS_INI_PATH.exists():
            cp.read(SETTINGS_INI_PATH, encoding="utf-8")
        if "youtube" not in cp:
            cp["youtube"] = {}
        clean_proxy = proxy.strip()
        if clean_proxy:
            cp["youtube"]["proxy"] = clean_proxy
        elif "proxy" in cp["youtube"]:
            del cp["youtube"]["proxy"]
        with open(SETTINGS_INI_PATH, "w", encoding="utf-8") as f:
            cp.write(f)

DEFAULT_PROXY_PRESETS = [
    {
        "id": "webshare_uk",
        "name": "🇬🇧 Webshare UK — London (Primary)",
        "url": os.getenv("SYNCSTATION_PROXY_UK", "http://username:password@p-uk.webshare.io:80"),
        "country": "GB",
        "city": "London"
    },
    {
        "id": "webshare_de",
        "name": "🇩🇪 Webshare Germany — Frankfurt (Backup)",
        "url": os.getenv("SYNCSTATION_PROXY_DE", "http://username:password@p-de.webshare.io:80"),
        "country": "DE",
        "city": "Frankfurt"
    },
    {
        "id": "webshare_es",
        "name": "🇪🇸 Webshare Spain — Madrid",
        "url": os.getenv("SYNCSTATION_PROXY_ES", "http://username:password@p-es.webshare.io:80"),
        "country": "ES",
        "city": "Madrid"
    },
    {
        "id": "webshare_us",
        "name": "🇺🇸 Webshare USA — Los Angeles",
        "url": os.getenv("SYNCSTATION_PROXY_US", "http://username:password@p-us.webshare.io:80"),
        "country": "US",
        "city": "Los Angeles"
    }
]

save_deezer_token = AppConfig.save_deezer_token
save_deezer_arl = AppConfig.save_deezer_arl
save_deezer_app_credentials = AppConfig.save_deezer_app_credentials
save_deezer_proxy = AppConfig.save_deezer_proxy
save_youtube_proxy = AppConfig.save_youtube_proxy
