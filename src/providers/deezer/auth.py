import os
import time
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from typing import Dict, Any, Optional
import requests

from src.core.config import AppConfig, save_deezer_token, save_deezer_arl, save_deezer_app_credentials
from src.core.logger import logger
from src.providers.deezer.client import DeezerClient, DeezerAuthError

DEEZER_AUTH_URL = "https://connect.deezer.com/oauth/auth.php"
DEEZER_TOKEN_URL = "https://connect.deezer.com/oauth/access_token.php"
DEFAULT_PERMISSIONS = "basic_access,manage_library,delete_library,offline_access"

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _OAuthCallbackHandler.code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <body style="font-family: sans-serif; background: #0e0e10; color: #fff; text-align: center; padding-top: 50px;">
                <h1 style="color: #00ff87;">&#10004; Deezer Authentication Successful!</h1>
                <p>You can close this tab and return to SyncStation in your terminal.</p>
            </body>
            </html>
            """)
        else:
            _OAuthCallbackHandler.error = params.get("error_reason", ["Unknown error"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <body style="font-family: sans-serif; background: #0e0e10; color: #fff; text-align: center; padding-top: 50px;">
                <h1 style="color: #ff5f5f;">&#10008; Authentication Cancelled or Failed</h1>
                <p>Please check your credentials and try again.</p>
            </body>
            </html>
            """)

    def log_message(self, format, *args):
        # Suppress standard HTTP request logging to console
        pass


class DeezerAuthManager:
    """
    Handles authentication flows for Deezer:
    1. Direct API Access Token
    2. Local OAuth 2.0 Loopback (http://localhost:8080/callback)
    3. Deezer Browser ARL Cookie
    """

    @classmethod
    def get_token(cls) -> Optional[str]:
        env_tok = os.environ.get("DEEZER_TOKEN")
        if env_tok:
            return env_tok.strip()
        settings = AppConfig.get_settings()
        return settings.get("deezer", {}).get("access_token") or None

    @classmethod
    def get_arl(cls) -> Optional[str]:
        env_arl = os.environ.get("DEEZER_ARL")
        if env_arl:
            return env_arl.strip()
        settings = AppConfig.get_settings()
        return settings.get("deezer", {}).get("arl") or None

    @classmethod
    def get_proxy(cls) -> Optional[str]:
        env_proxy = os.environ.get("DEEZER_PROXY")
        if env_proxy:
            return env_proxy.strip()
        settings = AppConfig.get_settings()
        return settings.get("deezer", {}).get("proxy") or None

    @classmethod
    def test_connection(cls, token: Optional[str] = None, arl: Optional[str] = None, proxy: Optional[str] = None) -> Dict[str, Any]:
        tok = token or cls.get_token()
        arl_val = arl or cls.get_arl()
        proxy_val = proxy or cls.get_proxy()

        # 1. Try OAuth / direct API access token if configured
        if tok:
            client = DeezerClient(access_token=tok, proxy=proxy_val)
            try:
                user = client.get_user_me()
                user_name = user.get("name", "Unknown")
                user_id = str(user.get("id", ""))
                return {
                    "connected": True,
                    "auth_type": "token",
                    "user_name": user_name,
                    "user_id": user_id,
                    "message": f"Connected as {user_name} (ID: {user_id})"
                }
            except DeezerAuthError as e:
                if not arl_val:
                    return {
                        "connected": False,
                        "message": f"Deezer authentication expired or invalid: {e}"
                    }
            except Exception as e:
                if not arl_val:
                    return {
                        "connected": False,
                        "message": f"Could not connect to Deezer API: {e}"
                    }

        # 2. Try ARL browser session cookie if configured
        if arl_val:
            client = DeezerClient(arl=arl_val, proxy=proxy_val)
            test = client.test_arl()
            if test.get("connected"):
                return test
            return {
                "connected": False,
                "message": test.get("message", "Invalid or expired Deezer ARL cookie.")
            }

        return {
            "connected": False,
            "message": "No Deezer access token or ARL session configured."
        }

    @classmethod
    def login_with_token(cls, token: str) -> Dict[str, Any]:
        """Validates and persists a direct Deezer access token."""
        test = cls.test_connection(token=token.strip())
        if test.get("connected"):
            AppConfig.save_deezer_token(
                access_token=token.strip(),
                user_id=str(test.get("user_id", "")),
                user_name=str(test.get("user_name", "")),
                auth_type="token"
            )
            logger.info(f"Deezer token authenticated for user '{test.get('user_name')}'.")
            try:
                from src.providers.factory import DestinationRegistry
                DestinationRegistry.invalidate_status_cache()
            except Exception:
                pass
        return test

    @classmethod
    def login_with_arl(cls, arl: str, proxy: Optional[str] = None) -> Dict[str, Any]:
        """Validates and saves a Deezer ARL cookie."""
        clean_arl = arl.strip()
        proxy_val = proxy or cls.get_proxy()
        client = DeezerClient(arl=clean_arl, proxy=proxy_val)
        test = client.test_arl()
        if test.get("connected"):
            user_id = str(test.get("user_id", ""))
            user_name = str(test.get("user_name", "Deezer User"))
            AppConfig.save_deezer_arl(clean_arl)
            AppConfig.save_deezer_token(
                access_token="",
                user_id=user_id,
                user_name=user_name,
                auth_type="arl"
            )
            logger.info(f"Deezer ARL session authenticated for '{user_name}' (ID: {user_id}).")
            try:
                from src.providers.factory import DestinationRegistry
                DestinationRegistry.invalidate_status_cache()
            except Exception:
                pass
            return test
        return test

    @classmethod
    def login_with_oauth(
        cls,
        app_id: str,
        app_secret: str,
        port: int = 8080,
        timeout_seconds: int = 120
    ) -> Dict[str, Any]:
        """
        Launches local loopback server and opens user's browser for standard Deezer OAuth 2.0 flow.
        """
        _OAuthCallbackHandler.code = None
        _OAuthCallbackHandler.error = None

        redirect_uri = f"http://localhost:{port}/callback"
        auth_params = {
            "app_id": app_id.strip(),
            "redirect_uri": redirect_uri,
            "perms": DEFAULT_PERMISSIONS,
            "response_type": "code"
        }
        auth_url = f"{DEEZER_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

        server = HTTPServer(("localhost", port), _OAuthCallbackHandler)
        server_thread = threading.Thread(target=server.handle_request)
        server_thread.daemon = True
        server_thread.start()

        logger.info(f"Opening browser for Deezer authorization: {auth_url}")
        webbrowser.open(auth_url)

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if _OAuthCallbackHandler.code or _OAuthCallbackHandler.error:
                break
            time.sleep(0.5)

        server.server_close()

        if _OAuthCallbackHandler.error:
            return {
                "connected": False,
                "message": f"Deezer OAuth failed: {_OAuthCallbackHandler.error}"
            }

        if not _OAuthCallbackHandler.code:
            return {
                "connected": False,
                "message": "Deezer OAuth timed out waiting for browser authorization."
            }

        # Exchange authorization code for access token
        token_params = {
            "app_id": app_id.strip(),
            "secret": app_secret.strip(),
            "code": _OAuthCallbackHandler.code,
            "output": "json"
        }
        token_resp = requests.get(DEEZER_TOKEN_URL, params=token_params, timeout=10)
        try:
            token_data = token_resp.json()
        except Exception:
            token_data = dict(urllib.parse.parse_qsl(token_resp.text))

        access_token = token_data.get("access_token")
        if not access_token:
            err_msg = token_data.get("error", {}).get("message", token_resp.text)
            return {
                "connected": False,
                "message": f"Failed to obtain Deezer access token: {err_msg}"
            }

        AppConfig.save_deezer_app_credentials(app_id, app_secret)
        return cls.login_with_token(access_token)
