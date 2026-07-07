import re
import json
import html
import requests
from typing import List
from src.core.models import Playlist, Track
from src.providers.base import MusicSource
from src.core.logger import logger

class SpotifyWebSource(MusicSource):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def get_playlists(self) -> List[Playlist]:
        return []

    def get_playlist_tracks(self, playlist_id_or_url: str) -> Playlist:
        p_id = playlist_id_or_url.split("playlist/")[-1].split("?")[0] if "playlist/" in playlist_id_or_url else playlist_id_or_url
        embed_url = f"https://open.spotify.com/embed/playlist/{p_id}"
        
        try:
            r = requests.get(embed_url, headers=self.headers, timeout=15)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} fetching embed page")

            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
            if not match:
                raise RuntimeError("Could not find track data in embed")

            data = json.loads(match.group(1))
            entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
            name = entity.get("name", "Spotify Playlist")
            desc = entity.get("description", "")
            raw_tracks = entity.get("trackList", [])

            tracks = []
            for t in raw_tracks:
                title = t.get("title", "").strip()
                subtitle = t.get("subtitle", "").strip()
                duration = t.get("duration", 0) / 1000.0
                tracks.append(Track(
                    id=t.get("uri", "").replace("spotify:track:", ""),
                    uri=t.get("uri", ""),
                    name=title,
                    artist=subtitle,
                    album="",
                    duration_seconds=duration
                ))

            return Playlist(
                id=p_id,
                uri=f"spotify:playlist:{p_id}",
                name=name,
                description=html.unescape(desc) if desc else "",
                track_count=len(tracks),
                tracks=tracks
            )
        except Exception as e:
            logger.error(f"Error scraping Spotify web: {e}")
            return Playlist(id=p_id, uri=f"spotify:playlist:{p_id}", name=p_id, tracks=[])

    def get_liked_tracks(self) -> List[Track]:
        return []
