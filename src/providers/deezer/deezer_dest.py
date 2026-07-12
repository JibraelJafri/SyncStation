import time
from typing import List, Dict, Any, Optional
from src.core.models import Track, Playlist, CandidateTrack
from src.core.logger import logger
from src.domain.normalizer import clean_query_string, strip_edition_noise, extract_title_and_featured
from src.providers.base import MusicDestination
from src.providers.deezer.client import DeezerClient, DeezerAPIError
from src.providers.deezer.auth import DeezerAuthManager

class DeezerDestination(MusicDestination):
    """
    Deezer Music Destination Provider.
    Implements search, playlist creation, track addition, and library sync
    against the Deezer REST API.
    """

    def __init__(self, access_token: Optional[str] = None, arl: Optional[str] = None, proxy: Optional[str] = None):
        self.access_token = access_token or DeezerAuthManager.get_token()
        self.arl = arl or DeezerAuthManager.get_arl()
        self.proxy = proxy or DeezerAuthManager.get_proxy()
        self.client = DeezerClient(access_token=self.access_token, arl=self.arl, proxy=self.proxy)

    def test_connection(self) -> Dict[str, Any]:
        """Verify API connectivity and authentication status."""
        return DeezerAuthManager.test_connection(token=self.access_token, arl=self.arl, proxy=self.proxy)

    def is_available(self) -> bool:
        """Returns True if Deezer authentication (access token or ARL cookie) is configured."""
        return bool(self.access_token or self.arl)

    def get_library_playlists(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch all user playlists from Deezer library."""
        try:
            raw_list = self.client.get_all_user_playlists(limit=limit)
            return [
                {
                    "id": str(p.get("id")),
                    "playlistId": str(p.get("id")),
                    "title": p.get("title", ""),
                    "count": p.get("nb_tracks", 0)
                }
                for p in raw_list
            ]
        except Exception as e:
            logger.warning(f"Could not fetch Deezer library playlists: {e}")
            return []

    def _convert_deezer_track(self, raw: Dict[str, Any]) -> CandidateTrack:
        """Converts raw Deezer track JSON into standardized CandidateTrack."""
        artist_name = ""
        if isinstance(raw.get("artist"), dict):
            artist_name = raw["artist"].get("name", "")
        elif isinstance(raw.get("artist"), str):
            artist_name = raw["artist"]

        album_title = ""
        thumbnail = None
        if isinstance(raw.get("album"), dict):
            album_title = raw["album"].get("title", "")
            thumbnail = raw["album"].get("cover_medium") or raw["album"].get("cover_small")

        return CandidateTrack(
            video_id=str(raw["id"]),
            title=raw.get("title", "").strip(),
            artist=artist_name.strip(),
            album=album_title.strip(),
            duration_seconds=float(raw.get("duration", 0.0)),
            result_type="song",
            thumbnail_url=thumbnail,
            is_explicit=bool(raw.get("explicit_lyrics", False)),
            isrc=raw.get("isrc")
        )

    def search_candidates(self, track: Track, limit: int = 5) -> List[CandidateTrack]:
        """
        4-Stage Search Strategy for Deezer:
        1. Exact ISRC Lookup: If source has ISRC, look up directly (/track/isrc:{isrc}).
        2. Advanced Field Search: artist:"..." track:"...".
        3. Fuzzy Search: clean_query_string(track.name, track.artist).
        4. Clean Title Search: fallback to stripped track title.
        """
        candidates: List[CandidateTrack] = []
        seen_ids = set()

        # ─── Stage 1: Exact ISRC Direct Resolution ─────────────────────────
        if track.isrc:
            try:
                isrc_track = self.client.get_track_by_isrc(track.isrc)
                if isrc_track and "id" in isrc_track:
                    cand = self._convert_deezer_track(isrc_track)
                    candidates.append(cand)
                    seen_ids.add(cand.video_id)
                    # If ISRC resolves and duration matches within 5s, we can return early
                    if track.duration_seconds > 0 and abs(track.duration_seconds - cand.duration_seconds) <= 5:
                        return candidates
            except Exception as e:
                logger.debug(f"Deezer ISRC lookup failed for {track.isrc}: {e}")

        # Extract cleaned title and artist variants
        clean_title, _ = extract_title_and_featured(track.name)
        clean_title = strip_edition_noise(clean_title)

        artists_to_try = [track.artist]
        multi_artists = [a.strip() for a in track.artist.split(",") if a.strip()]
        if len(multi_artists) > 1:
            artists_to_try.extend(multi_artists)

        # ─── Stage 2: Field-Level Advanced Search ─────────────────────────
        for art in artists_to_try:
            try:
                raw_results = self.client.search_advanced(
                    artist=art,
                    track=clean_title,
                    limit=3
                )
                for item in raw_results:
                    tid = str(item.get("id"))
                    if tid and tid not in seen_ids:
                        candidates.append(self._convert_deezer_track(item))
                        seen_ids.add(tid)
                if len(candidates) >= limit * 2:
                    break
            except Exception as e:
                logger.debug(f"Deezer advanced search failed: {e}")

        # ─── Stage 3: General Query Fuzzy Search ──────────────────────────
        if len(candidates) < limit:
            queries_to_try = [clean_query_string(track.name, track.artist)]
            if len(multi_artists) > 1:
                queries_to_try.append(f"{multi_artists[0]} {clean_title}")
                if len(multi_artists) > 1:
                    queries_to_try.append(f"{multi_artists[1]} {clean_title}")

            for q in queries_to_try:
                try:
                    raw_results = self.client.search_tracks(query=q, limit=limit)
                    for item in raw_results:
                        tid = str(item.get("id"))
                        if tid and tid not in seen_ids:
                            candidates.append(self._convert_deezer_track(item))
                            seen_ids.add(tid)
                    if len(candidates) >= limit:
                        break
                except Exception as e:
                    logger.debug(f"Deezer fuzzy search failed for '{q}': {e}")

        # ─── Stage 4: Clean Title Fallback ────────────────────────────────
        if not candidates and clean_title:
            try:
                raw_results = self.client.search_tracks(query=clean_title, limit=limit)
                for item in raw_results:
                    tid = str(item.get("id"))
                    if tid and tid not in seen_ids:
                        candidates.append(self._convert_deezer_track(item))
                        seen_ids.add(tid)
            except Exception as e:
                logger.debug(f"Deezer fallback search failed: {e}")

        return candidates[:limit]

    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        """Fetch Deezer playlist metadata and all tracks."""
        clean_id = playlist_id.split("/")[-1].strip()
        raw_pl = self.client.get_playlist(clean_id)
        if not raw_pl or "id" not in raw_pl:
            return None

        raw_tracks = []
        if isinstance(raw_pl.get("tracks"), dict):
            raw_tracks = raw_pl["tracks"].get("data", [])

        if not raw_tracks:
            raw_tracks = self.client.get_all_playlist_tracks(clean_id)

        tracks: List[Track] = []
        for idx, t in enumerate(raw_tracks):
            raw_id = t.get("id") if t.get("id") is not None else t.get("SNG_ID")
            tid = str(raw_id).strip() if raw_id is not None else ""
            title = (t.get("title") or t.get("SNG_TITLE") or "").strip()
            
            art_name = ""
            if isinstance(t.get("artist"), dict):
                art_name = t["artist"].get("name", "")
            elif isinstance(t.get("artist"), str):
                art_name = t["artist"]
            elif t.get("ART_NAME"):
                art_name = t.get("ART_NAME")

            alb_name = ""
            if isinstance(t.get("album"), dict):
                alb_name = t["album"].get("title", "")
            elif isinstance(t.get("album"), str):
                alb_name = t["album"]
            elif t.get("ALB_TITLE"):
                alb_name = t.get("ALB_TITLE")

            dur = float(t.get("duration") or t.get("DURATION") or 0.0)
            isrc = t.get("isrc") or t.get("ISRC")

            if tid and title:
                tracks.append(Track(
                    id=tid,
                    uri=f"deezer:track:{tid}",
                    name=title,
                    artist=art_name.strip(),
                    album=alb_name.strip(),
                    duration_seconds=dur,
                    isrc=isrc
                ))
        server_nb = int(raw_pl.get("nb_tracks") or raw_pl.get("NB_SONG") or 0)
        nb_tracks = max(len(tracks), server_nb)
        if server_nb > len(tracks):
            logger.warning(f"Deezer playlist {clean_id} reports {server_nb} tracks on server, but only {len(tracks)} were fetched.")

        return Playlist(
            id=str(raw_pl["id"]),
            name=raw_pl.get("title", "Deezer Playlist"),
            description=raw_pl.get("description", ""),
            track_count=nb_tracks,
            tracks=tracks,
            source_type="deezer",
            image_url=raw_pl.get("picture_medium") or raw_pl.get("picture_small")
        )

    def create_playlist(
        self,
        name: str,
        description: str = "",
        privacy: str = "PRIVATE",
        video_ids: List[str] = None
    ) -> str:
        """Create a new playlist on Deezer and optionally add tracks."""
        pl_id = self.client.create_playlist(title=name)
        if video_ids:
            self.add_tracks_to_playlist(pl_id, video_ids)
        return pl_id

    def add_tracks_to_playlist(self, playlist_id: str, video_ids: List[str]) -> int:
        """Add tracks to Deezer playlist in polite chunks."""
        clean_id = playlist_id.split("/")[-1].strip()
        return self.client.add_tracks_to_playlist(clean_id, video_ids)

    def remove_tracks_from_playlist(self, playlist_id: str, video_ids: List[str]) -> int:
        """Remove tracks from Deezer playlist."""
        clean_id = playlist_id.split("/")[-1].strip()
        return self.client.remove_tracks_from_playlist(clean_id, video_ids)

    def rate_track(self, video_id: str, rating: str = "LIKE") -> bool:
        """Add track to user's favorite tracks on Deezer."""
        try:
            if self.access_token:
                res = self.client.request(
                    "POST",
                    "/user/me/tracks",
                    params={"track_id": video_id},
                    use_auth=True
                )
                return bool(res is True or res == 1 or (isinstance(res, dict) and res.get("status") == 200))

            if self.arl:
                csrf = self.client._ensure_csrf_token()
                resp = self.client.session.post(
                    f"https://www.deezer.com/ajax/gw-light.php?method=favorite_song.add&api_version=1.0&api_token={csrf}",
                    json={"SNG_ID": int(video_id)},
                    timeout=5.0
                )
                data = resp.json()
                return bool(data.get("results"))

            return False
        except Exception as e:
            logger.debug(f"Failed to like track {video_id} on Deezer: {e}")
            return False
