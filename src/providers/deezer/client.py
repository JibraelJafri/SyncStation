import os
import time
import random
import urllib.parse
from typing import Dict, Any, Optional, List, Union
import requests
from src.core.logger import logger

DEEZER_API_BASE = "https://api.deezer.com"

class DeezerAPIError(Exception):
    """Base exception for Deezer API failures."""
    def __init__(self, message: str, code: Optional[int] = None, error_type: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.error_type = error_type

class DeezerAuthError(DeezerAPIError):
    """Exception raised when authentication fails or token expires."""
    pass

class DeezerRateLimitError(DeezerAPIError):
    """Exception raised when Deezer API quota is exceeded."""
    pass

class DeezerClient:
    """
    High-resilience HTTP Client for Deezer REST API & Web Gateway.
    Features:
    - Dual auth engine: Direct OAuth access token AND/OR browser ARL cookie session
    - Automatic rate-limit pacing (preventing 50 req / 5s quota trips)
    - Exponential backoff with jitter on HTTP 429 and Deezer error code 4
    - Direct ISRC lookups (/track/isrc:{isrc})
    - Field-level advanced search (q=artist:"..." track:"...")
    - Chunked batch playlist operations (up to 50 tracks/request)
    """

    def __init__(self, access_token: Optional[str] = None, arl: Optional[str] = None, proxy: Optional[str] = None):
        self.access_token = access_token.strip() if access_token else None
        self.arl = arl.strip() if arl else None
        self.proxy = (proxy or os.environ.get("DEEZER_PROXY") or "").strip() or None
        if not self.proxy:
            try:
                from src.core.config import AppConfig
                self.proxy = AppConfig.get_settings().get("deezer", {}).get("proxy") or None
            except Exception:
                pass
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        })
        if self.proxy:
            self.session.proxies.update({
                "http": self.proxy,
                "https": self.proxy
            })
        if self.arl:
            self.session.cookies.set("arl", self.arl, domain=".deezer.com")

        self._last_request_time = 0.0
        self._min_interval = 0.12  # Pacing: ~8 req/sec max to stay safely under 50 req / 5s

        self.user_id: Optional[str] = None
        self.user_name: Optional[str] = None
        self.csrf_token: Optional[str] = None

    def _wait_pacing(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def set_token(self, token: Optional[str]):
        self.access_token = token.strip() if token else None

    def set_arl(self, arl: Optional[str]):
        self.arl = arl.strip() if arl else None
        if self.arl:
            self.session.cookies.set("arl", self.arl, domain=".deezer.com")
            self.csrf_token = None

    def test_arl(self, arl: Optional[str] = None) -> Dict[str, Any]:
        """Validates Deezer browser ARL cookie via internal gw-light endpoint."""
        target_arl = (arl or self.arl or "").strip()
        if not target_arl:
            return {"connected": False, "message": "No Deezer ARL cookie provided."}

        self.arl = target_arl
        self.session.cookies.set("arl", self.arl, domain=".deezer.com")
        try:
            resp = self.session.get(
                "https://www.deezer.com/ajax/gw-light.php?method=deezer.getUserData&api_version=1.0&api_token=",
                timeout=4.0
            )
            data = resp.json()
            results = data.get("results", {})
            user = results.get("USER", {})
            if user and user.get("USER_ID"):
                self.user_id = str(user.get("USER_ID"))
                self.user_name = user.get("BLOG_NAME") or "Deezer User"
                self.csrf_token = results.get("checkForm", "")
                return {
                    "connected": True,
                    "auth_type": "arl",
                    "user_id": self.user_id,
                    "user_name": self.user_name,
                    "message": f"Connected via ARL as {self.user_name} (ID: {self.user_id})"
                }
            return {
                "connected": False,
                "message": "Invalid or expired Deezer ARL cookie (could not fetch user session)."
            }
        except Exception as e:
            return {
                "connected": False,
                "message": f"Deezer ARL authentication error: {e}"
            }

    def _ensure_csrf_token(self) -> str:
        """Retrieves CSRF checkForm token for web gateway calls if not cached."""
        if self.csrf_token:
            return self.csrf_token
        test = self.test_arl()
        if test.get("connected") and self.csrf_token:
            return self.csrf_token
        raise DeezerAuthError("Failed to obtain Deezer CSRF checkForm token using ARL session.")

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        use_auth: bool = False,
        max_retries: int = 4
    ) -> Dict[str, Any]:
        """
        Executes an HTTP request against Deezer API with automatic retries,
        quota backoff, and error translation.
        """
        url = endpoint if endpoint.startswith("http") else f"{DEEZER_API_BASE}{endpoint}"
        query_params = dict(params or {})

        if use_auth and self.access_token:
            query_params["access_token"] = self.access_token

        for attempt in range(max_retries + 1):
            self._wait_pacing()
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=query_params,
                    data=data,
                    timeout=10.0
                )

                # Deezer returns HTTP 200 with JSON errors in the body
                try:
                    payload = resp.json()
                except Exception:
                    resp.raise_for_status()
                    return {"status": resp.status_code}

                if isinstance(payload, dict) and "error" in payload:
                    err = payload["error"]
                    code = err.get("code")
                    error_type = err.get("type", "")
                    message = err.get("message", "Unknown Deezer error")

                    # Rate Limit / Quota Exceeded (Deezer error code 4) or HTTP 429
                    if code == 4 or resp.status_code == 429 or "quota" in message.lower():
                        if attempt < max_retries:
                            backoff = (1.5 ** attempt) + random.uniform(0.2, 0.8)
                            logger.warning(f"Deezer rate limit reached ({message}). Backing off for {backoff:.2f}s...")
                            time.sleep(backoff)
                            continue
                        raise DeezerRateLimitError(message, code=code, error_type=error_type)

                    # Authentication / Token error (code 200/300 or OAuthException)
                    if code in [200, 300] or "oauth" in error_type.lower() or "token" in message.lower():
                        raise DeezerAuthError(f"Deezer authentication error: {message}", code=code, error_type=error_type)

                    # Data not found / item missing (code 800)
                    if code == 800:
                        return {"error": err, "data": []}

                    raise DeezerAPIError(f"Deezer API error ({code}): {message}", code=code, error_type=error_type)

                return payload

            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.debug(f"Deezer network glitch ({e}). Retrying in {backoff:.2f}s...")
                    time.sleep(backoff)
                    continue
                raise DeezerAPIError(f"Deezer network connection failure: {e}")

        raise DeezerAPIError("Max retries exceeded for Deezer request.")

    # ─── SEARCH & RESOLUTION (Public Endpoints) ──────────────────────────

    def search_tracks(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """General fuzzy track search."""
        if not query or not query.strip():
            return []
        try:
            res = self.request("GET", "/search", params={"q": query.strip(), "limit": limit})
            return res.get("data", [])
        except DeezerAPIError:
            return []

    def search_advanced(self, artist: str, track: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Field-specific advanced search."""
        parts = []
        if artist:
            clean_art = artist.replace('"', '').strip()
            parts.append(f'artist:"{clean_art}"')
        if track:
            clean_trk = track.replace('"', '').strip()
            parts.append(f'track:"{clean_trk}"')

        if not parts:
            return []

        query = " ".join(parts)
        try:
            res = self.request("GET", "/search", params={"q": query, "limit": limit})
            return res.get("data", [])
        except DeezerAPIError:
            return []

    def get_track_by_isrc(self, isrc: str) -> Optional[Dict[str, Any]]:
        """Exact ISRC Direct Lookup (/track/isrc:{isrc})."""
        if not isrc or not isrc.strip():
            return None
        clean_isrc = isrc.strip().upper()
        try:
            res = self.request("GET", f"/track/isrc:{clean_isrc}")
            if res and isinstance(res, dict) and "id" in res:
                return res
            return None
        except DeezerAPIError:
            return None

    # ─── PLAYLISTS & LIBRARY OPERATIONS ─────────────────────────────────

    def get_playlist(self, playlist_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        """Fetch playlist metadata and all tracks."""
        clean_pid = str(playlist_id).split("/")[-1].strip()

        # 1. Primary for ARL: Use web session gw-light.
        # This provides full unredacted catalog tracks, user permissions, and reliable multi-page pagination.
        if self.arl:
            try:
                csrf = self._ensure_csrf_token()
                resp = self.session.post(
                    f"https://www.deezer.com/ajax/gw-light.php?method=deezer.pagePlaylist&api_version=1.0&api_token={csrf}",
                    json={"playlist_id": str(clean_pid), "lang": "en", "nb": 500, "start": 0},
                    timeout=10.0
                )
                data = resp.json()
                results = data.get("results", {})
                pl_data = results.get("DATA", {})
                if pl_data and pl_data.get("PLAYLIST_ID"):
                    songs_data = results.get("SONGS", {})
                    all_songs = list(songs_data.get("data", []))
                    total_positions = int(pl_data.get("NB_SONG", 0))

                    # If playlist has more tracks/positions than returned in initial page, paginate with playlist.getSongs
                    pos = len(all_songs) + int(songs_data.get("filtered_count", 0))
                    while pos < total_positions:
                        try:
                            p_resp = self.session.post(
                                f"https://www.deezer.com/ajax/gw-light.php?method=playlist.getSongs&api_version=1.0&api_token={csrf}",
                                json={"playlist_id": int(clean_pid), "nb": 500, "start": pos},
                                timeout=10.0
                            )
                            p_data = p_resp.json()
                            p_results = p_data.get("results", {})
                            batch = p_results.get("data", [])
                            if batch:
                                all_songs.extend(batch)
                            total_positions = int(p_results.get("total", total_positions))
                            pos += max(len(batch) + int(p_results.get("filtered_count", 0)), 500)
                            if not batch or len(batch) < 500:
                                break
                        except Exception as e_page:
                            logger.debug(f"Error fetching subsequent songs page via gw-light: {e_page}")
                            break

                    songs_data["data"] = all_songs
                    songs_data["count"] = len(all_songs)
                    songs_data["total"] = len(all_songs)

                    return {
                        "id": str(pl_data["PLAYLIST_ID"]),
                        "title": pl_data.get("TITLE", ""),
                        "description": pl_data.get("DESCRIPTION", ""),
                        "nb_tracks": len(all_songs),
                        "tracks": songs_data
                    }
            except Exception as e:
                logger.debug(f"Deezer gw-light get_playlist failed: {e}")

        # 2. REST API fallback or primary if OAuth access token
        try:
            res = self.request("GET", f"/playlist/{clean_pid}", use_auth=bool(self.access_token))
            if "id" in res:
                return res
        except (DeezerAPIError, DeezerAuthError):
            pass

        return None

    def get_playlist_tracks(self, playlist_id: Union[str, int], limit: int = 100, index: int = 0) -> List[Dict[str, Any]]:
        """Fetch a page of tracks for a playlist."""
        clean_pid = str(playlist_id).split("/")[-1].strip()
        try:
            res = self.request("GET", f"/playlist/{clean_pid}/tracks", params={"limit": limit, "index": index}, use_auth=bool(self.access_token))
            return res.get("data", [])
        except DeezerAPIError:
            return []

    def get_all_playlist_tracks(self, playlist_id: Union[str, int], max_tracks: int = 10000) -> List[Dict[str, Any]]:
        """Paginate and fetch all tracks in a playlist (dual engine: REST API or ARL session)."""
        clean_pid = str(playlist_id).split("/")[-1].strip()

        # A) If ARL cookie is configured and no OAuth access token, retrieve tracks via web gateway
        if self.arl and not self.access_token:
            pl = self.get_playlist(clean_pid)
            if pl and isinstance(pl.get("tracks"), dict):
                return pl["tracks"].get("data", [])
            return []

        # B) Direct REST API with OAuth token / public access
        all_tracks = []
        url = f"/playlist/{clean_pid}/tracks?limit=100&index=0"

        while url and len(all_tracks) < max_tracks:
            try:
                res = self.request("GET", url, use_auth=bool(self.access_token))
            except DeezerAPIError:
                break

            if not isinstance(res, dict):
                break

            batch = res.get("data", [])
            if not batch:
                break

            # Preserve all tracks including duplicates so callers can inspect and reconcile
            all_tracks.extend(batch)

            next_url = res.get("next")
            if not next_url:
                total = res.get("total", 0)
                if len(all_tracks) < total:
                    url = f"/playlist/{clean_pid}/tracks?limit=100&index={len(all_tracks)}"
                else:
                    break
            else:
                if "api.deezer.com" in next_url:
                    url = next_url.split("api.deezer.com")[-1]
                else:
                    url = next_url

        return all_tracks

    def create_playlist(self, title: str) -> str:
        """Create a new playlist in user's library (supports both OAuth token and ARL cookie)."""
        # A) Direct API Access Token
        if self.access_token:
            res = self.request("POST", "/user/me/playlists", params={"title": title}, use_auth=True)
            if "id" in res:
                return str(res["id"])
            raise DeezerAPIError(f"Failed to create playlist via API token: {res}")

        # B) ARL Cookie Web Session
        if self.arl:
            csrf = self._ensure_csrf_token()
            try:
                resp = self.session.post(
                    f"https://www.deezer.com/ajax/gw-light.php?method=playlist.create&api_version=1.0&api_token={csrf}",
                    json={"title": title, "status": 0, "description": "Mirrored by SyncStation", "songs": []},
                    timeout=10.0
                )
                data = resp.json()
                if isinstance(data, dict) and data.get("results"):
                    return str(data["results"])
                raise DeezerAPIError(f"Failed to create playlist via ARL session: {data}")
            except Exception as e:
                raise DeezerAPIError(f"ARL playlist creation error: {e}")

        raise DeezerAuthError("No Deezer access token or ARL cookie configured.")

    def add_tracks_to_playlist(self, playlist_id: Union[str, int], track_ids: List[Union[str, int]]) -> int:
        """Add track IDs to playlist in batches of up to 50."""
        if not track_ids:
            return 0

        # Preserve order while deduplicating in-flight additions
        unique_track_ids = list(dict.fromkeys(track_ids))

        # A) Direct API Access Token
        if self.access_token:
            added = 0
            chunk_size = 50
            for i in range(0, len(unique_track_ids), chunk_size):
                chunk = unique_track_ids[i:i + chunk_size]
                songs_str = ",".join(str(tid) for tid in chunk)
                res = self.request(
                    "POST",
                    f"/playlist/{playlist_id}/tracks",
                    params={"songs": songs_str},
                    use_auth=True
                )
                if res is True or res == 1 or (isinstance(res, dict) and (res.get("status") == 200 or res.get("id"))):
                    added += len(chunk)
                else:
                    added += len(chunk)
            return added

        # B) ARL Cookie Web Session
        if self.arl:
            csrf = self._ensure_csrf_token()
            added = 0
            clean_track_ids = [str(tid).strip() for tid in unique_track_ids if str(tid).strip().isdigit()]
            if not clean_track_ids:
                logger.warning(f"No valid numeric Deezer track IDs provided in batch of {len(track_ids)}.")
                return 0

            chunk_size = 50
            for i in range(0, len(clean_track_ids), chunk_size):
                chunk = clean_track_ids[i:i + chunk_size]
                songs_payload = [[str(tid), 0] for tid in chunk]
                try:
                    res = self.session.post(
                        f"https://www.deezer.com/ajax/gw-light.php?method=playlist.addSongs&api_version=1.0&api_token={csrf}",
                        json={"playlist_id": int(playlist_id), "songs": songs_payload, "offset": -1},
                        timeout=15.0
                    )
                    data = res.json()
                    if data.get("results") is True or data.get("results"):
                        added += len(chunk)
                    elif isinstance(data.get("error"), dict) and data["error"].get("ERROR_DATA_EXISTS"):
                        # Batch contained a track that already exists in playlist; add individually
                        for tid in chunk:
                            try:
                                s_res = self.session.post(
                                    f"https://www.deezer.com/ajax/gw-light.php?method=playlist.addSongs&api_version=1.0&api_token={csrf}",
                                    json={"playlist_id": int(playlist_id), "songs": [[str(tid), 0]], "offset": -1},
                                    timeout=10.0
                                )
                                s_data = s_res.json()
                                if s_data.get("results") is True or s_data.get("results"):
                                    added += 1
                                elif isinstance(s_data.get("error"), dict) and s_data["error"].get("ERROR_DATA_EXISTS"):
                                    added += 1
                                else:
                                    logger.warning(f"Deezer failed to add track {tid}: {s_data}")
                            except Exception as e_single:
                                logger.warning(f"Error adding single Deezer track {tid}: {e_single}")
                    else:
                        logger.warning(f"Deezer gw-light addSongs failed: {data}")
                except Exception as e:
                    logger.warning(f"Error adding tracks to Deezer playlist via ARL: {e}")
            return added

        raise DeezerAuthError("No Deezer access token or ARL cookie configured.")

    def remove_tracks_from_playlist(self, playlist_id: Union[str, int], track_ids: List[Union[str, int]]) -> int:
        """Remove track IDs from playlist."""
        if not track_ids:
            return 0

        clean_pid = str(playlist_id).split("/")[-1].strip()
        clean_track_ids = [str(tid).strip() for tid in track_ids if str(tid).strip().isdigit()]
        if not clean_track_ids:
            return 0

        if self.access_token:
            removed = 0
            chunk_size = 50
            for i in range(0, len(clean_track_ids), chunk_size):
                chunk = clean_track_ids[i:i + chunk_size]
                songs_str = ",".join(chunk)
                try:
                    self.request(
                        "DELETE",
                        f"/playlist/{clean_pid}/tracks",
                        params={"songs": songs_str},
                        use_auth=True
                    )
                    removed += len(chunk)
                except Exception as e:
                    logger.warning(f"Error removing tracks from Deezer via REST API: {e}")
            return removed

        if self.arl:
            csrf = self._ensure_csrf_token()
            removed = 0
            chunk_size = 50
            for i in range(0, len(clean_track_ids), chunk_size):
                chunk = clean_track_ids[i:i + chunk_size]
                songs_payload = [[int(tid), 0] for tid in chunk]
                try:
                    res = self.session.post(
                        f"https://www.deezer.com/ajax/gw-light.php?method=playlist.deleteSongs&api_version=1.0&api_token={csrf}",
                        json={"playlist_id": int(clean_pid), "songs": songs_payload},
                        timeout=10.0
                    )
                    data = res.json()
                    if data.get("results") is True or data.get("results"):
                        removed += len(chunk)
                    elif not data.get("error"):
                        removed += len(chunk)
                    else:
                        logger.warning(f"Deezer gw-light deleteSongs returned error: {data.get('error')}")
                except Exception as e:
                    logger.warning(f"Error removing tracks from Deezer via ARL: {e}")
            return removed

        return 0

    def get_user_me(self) -> Dict[str, Any]:
        """Fetch current authenticated user profile."""
        if self.access_token:
            return self.request("GET", "/user/me", use_auth=True)
        if self.arl:
            test = self.test_arl()
            if test.get("connected"):
                return {"id": self.user_id, "name": self.user_name}
        raise DeezerAuthError("No Deezer credentials configured.")

    def get_user_playlists(self, limit: int = 100, index: int = 0) -> List[Dict[str, Any]]:
        """Fetch playlists belonging to the authenticated user."""
        if self.access_token:
            try:
                res = self.request("GET", "/user/me/playlists", params={"limit": limit, "index": index}, use_auth=True)
                return res.get("data", [])
            except Exception:
                return []
        if self.arl:
            if not self.user_id:
                self.test_arl()
            if self.user_id:
                try:
                    res = self.request("GET", f"/user/{self.user_id}/playlists", params={"limit": limit, "index": index})
                    return res.get("data", [])
                except Exception:
                    return []
        return []

    def get_all_user_playlists(self, limit: int = 100, max_playlists: int = 2000) -> List[Dict[str, Any]]:
        """Paginate and fetch all playlists belonging to the user."""
        all_playlists = []
        seen_ids = set()
        index = 0

        while len(all_playlists) < max_playlists:
            batch = self.get_user_playlists(limit=limit, index=index)
            if not batch:
                break

            new_in_batch = 0
            for p in batch:
                pid = str(p.get("id"))
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_playlists.append(p)
                    new_in_batch += 1

            if new_in_batch == 0 or len(batch) < limit:
                break

            index += len(batch)

        return all_playlists
