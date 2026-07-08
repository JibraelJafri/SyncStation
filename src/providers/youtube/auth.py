import re
import json
from ytmusicapi.setup import setup_browser
from src.core.config import AppConfig

def parse_and_save_headers(raw_input: str) -> bool:
    # If cURL command was pasted, extract cookie and headers
    cookie = None
    auth = None
    auth_user = "0"

    cookie_match = re.search(r"-(?:b|-cookie)\s+['\"](.*?)['\"]", raw_input, re.DOTALL)
    if not cookie_match:
        cookie_match = re.search(r"cookie:\s*(.*?)(?:\n|$)", raw_input, re.IGNORECASE)
    if cookie_match:
        cookie = cookie_match.group(1).strip()

    auth_match = re.search(r"authorization:\s*(.*?)(?:\n|$|['\"])", raw_input, re.IGNORECASE)
    if auth_match:
        auth = auth_match.group(1).strip()

    auth_user_match = re.search(r"x-goog-authuser:\s*(\d+)", raw_input, re.IGNORECASE)
    if auth_user_match:
        auth_user = auth_user_match.group(1).strip()

    if cookie:
        formatted_raw = f"cookie: {cookie}\nx-goog-authuser: {auth_user}\n"
        if auth:
            formatted_raw += f"authorization: {auth}\n"
        try:
            headers_json = setup_browser(headers_raw=formatted_raw)
            AppConfig.save_youtube_headers(headers_json, "browser")
            return True
        except Exception:
            pass

    # Try raw headers parsing fallback
    try:
        headers_json = setup_browser(headers_raw=raw_input)
        AppConfig.save_youtube_headers(headers_json, "browser")
        return True
    except Exception:
        return False
