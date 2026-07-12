import json
import pytest
from unittest.mock import patch, MagicMock

from src.core.models import Track, CandidateTrack
from src.providers.deezer.client import DeezerClient, DeezerAPIError, DeezerRateLimitError
from src.providers.deezer.auth import DeezerAuthManager
from src.providers.deezer.deezer_dest import DeezerDestination
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.providers.factory import DestinationRegistry

class TestDeezerClient:
    def test_get_track_by_isrc(self):
        client = DeezerClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 3135556,
            "title": "Harder, Better, Faster, Stronger",
            "isrc": "GBDUW0100003",
            "duration": 224,
            "artist": {"name": "Daft Punk"},
            "album": {"title": "Discovery", "cover_medium": "https://e-cdns-images.dzcdn.net/cover.jpg"}
        }

        with patch.object(client.session, "request", return_value=mock_response):
            track = client.get_track_by_isrc("GBDUW0100003")
            assert track is not None
            assert track["id"] == 3135556
            assert track["title"] == "Harder, Better, Faster, Stronger"
            assert track["artist"]["name"] == "Daft Punk"

    def test_isrc_not_found(self):
        client = DeezerClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "error": {"type": "DataException", "message": "no data", "code": 800}
        }

        with patch.object(client.session, "request", return_value=mock_response):
            track = client.get_track_by_isrc("INVALIDISRC999")
            assert track is None

    def test_rate_limiting_backoff(self):
        client = DeezerClient()
        error_resp = MagicMock()
        error_resp.status_code = 200
        error_resp.json.return_value = {
            "error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}
        }

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"id": 12345, "title": "Recovered Song"}

        with patch.object(client.session, "request", side_effect=[error_resp, ok_resp]), \
             patch("src.providers.deezer.client.time.sleep") as mock_sleep:
            res = client.request("GET", "/track/12345")
            assert res["id"] == 12345
            assert mock_sleep.called

    def test_chunked_add_tracks(self):
        client = DeezerClient(access_token="test_token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = True

        with patch.object(client.session, "request", return_value=mock_resp):
            vids = [str(i) for i in range(120)]
            added = client.add_tracks_to_playlist("9999", vids)
            assert added == 120

class TestDeezerDestination:
    def test_search_candidates_isrc_hit(self):
        dest = DeezerDestination(access_token="fake_token")
        mock_raw = {
            "id": 101,
            "title": "Starboy",
            "isrc": "USUG11600815",
            "duration": 230,
            "artist": {"name": "The Weeknd"},
            "album": {"title": "Starboy", "cover_medium": "https://thumb.jpg"},
            "explicit_lyrics": True
        }

        with patch.object(dest.client, "get_track_by_isrc", return_value=mock_raw):
            track = Track(
                id="sp1",
                uri="spotify:track:123",
                name="Starboy",
                artist="The Weeknd",
                album="Starboy",
                duration_seconds=230.0,
                isrc="USUG11600815"
            )
            cands = dest.search_candidates(track, limit=5)
            assert len(cands) == 1
            cand = cands[0]
            assert cand.video_id == "101"
            assert cand.destination_id == "101"
            assert cand.title == "Starboy"
            assert cand.artist == "The Weeknd"
            assert cand.is_explicit is True

    def test_search_candidates_fallback_to_advanced(self):
        dest = DeezerDestination(access_token="fake_token")
        mock_search_results = [
            {
                "id": 202,
                "title": "Blinding Lights",
                "duration": 200,
                "artist": {"name": "The Weeknd"},
                "album": {"title": "After Hours"},
                "explicit_lyrics": False
            }
        ]

        with patch.object(dest.client, "get_track_by_isrc", return_value=None), \
             patch.object(dest.client, "search_advanced", return_value=mock_search_results), \
             patch.object(dest.client, "search_tracks", return_value=[]):
            track = Track(
                id="sp2",
                uri="spotify:track:456",
                name="Blinding Lights",
                artist="The Weeknd",
                duration_seconds=200.0
            )
            cands = dest.search_candidates(track, limit=5)
            assert len(cands) == 1
            assert cands[0].video_id == "202"
            assert cands[0].title == "Blinding Lights"

    def test_get_playlist(self):
        dest = DeezerDestination(access_token="fake_token")
        mock_pl = {
            "id": 8888,
            "title": "Retro Waves",
            "description": "Synth vibes",
            "picture_medium": "https://img.jpg"
        }
        mock_tracks = [
            {"id": 1, "title": "Resonance", "artist": {"name": "HOME"}, "album": {"title": "Odyssey"}, "duration": 212},
            {"id": 2, "title": "Sunset", "artist": {"name": "The Midnight"}, "album": {"title": "Endless Summer"}, "duration": 326}
        ]

        with patch.object(dest.client, "get_playlist", return_value=mock_pl), \
             patch.object(dest.client, "get_all_playlist_tracks", return_value=mock_tracks):
            pl = dest.get_playlist("8888")
            assert pl is not None
            assert pl.id == "8888"
            assert pl.name == "Retro Waves"
            assert pl.track_count == 2
            assert pl.tracks[0].name == "Resonance"
            assert pl.tracks[1].artist == "The Midnight"

    def test_create_playlist(self):
        dest = DeezerDestination(access_token="fake_token")
        with patch.object(dest.client, "create_playlist", return_value="5555"), \
             patch.object(dest.client, "add_tracks_to_playlist", return_value=2):
            pl_id = dest.create_playlist("My Deezer Playlist", video_ids=["10", "20"])
            assert pl_id == "5555"

class TestDestinationRegistry:
    def test_get_destinations(self):
        yt = DestinationRegistry.get_destination("youtube")
        assert yt.__class__.__name__ == "YouTubeMusicDestination"

        dz = DestinationRegistry.get_destination("deezer")
        assert dz.__class__.__name__ == "DeezerDestination"

    def test_active_destination_persistence(self):
        DestinationRegistry.set_active_destination_name("deezer")
        assert DestinationRegistry.get_active_destination_name() == "deezer"

        # Restore default
        DestinationRegistry.set_active_destination_name("youtube")
        assert DestinationRegistry.get_active_destination_name() == "youtube"

class TestDeezerAuth:
    def test_token_auth_success(self):
        with patch("src.providers.deezer.auth.DeezerAuthManager.test_connection", return_value={"connected": True, "message": "Authenticated as TestUser (ID: 123)", "user_name": "TestUser", "user_id": "123"}), \
             patch("src.core.config.AppConfig.save_deezer_token"):
            res = DeezerAuthManager.login_with_token("valid_token")
            assert res["connected"] is True
            assert "TestUser" in res["message"]

    def test_token_auth_failure(self):
        with patch("src.providers.deezer.auth.DeezerAuthManager.test_connection", return_value={"connected": False, "message": "Invalid OAuth access token"}):
            res = DeezerAuthManager.login_with_token("bad_token")
            assert res["connected"] is False

    def test_arl_auth_success(self):
        with patch.object(DeezerClient, "test_arl", return_value={"connected": True, "auth_type": "arl", "user_name": "ArlUser", "user_id": "999", "message": "Connected via ARL as ArlUser (ID: 999)"}), \
             patch("src.core.config.AppConfig.save_deezer_arl"), \
             patch("src.core.config.AppConfig.save_deezer_token"):
            res = DeezerAuthManager.login_with_arl("dummy_arl_cookie_value")
            assert res["connected"] is True
            assert res["auth_type"] == "arl"
            assert "ArlUser" in res["message"]

    def test_arl_auth_failure(self):
        with patch.object(DeezerClient, "test_arl", return_value={"connected": False, "message": "Invalid or expired Deezer ARL cookie."}):
            res = DeezerAuthManager.login_with_arl("bad_arl")
            assert res["connected"] is False

    def test_client_create_playlist_via_arl(self):
        client = DeezerClient(arl="dummy_arl")
        client.csrf_token = "fake_csrf"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": 7777}

        with patch.object(client.session, "post", return_value=mock_resp):
            pl_id = client.create_playlist("ARL Created Playlist")
            assert pl_id == "7777"

    def test_destination_registry_status_caching(self):
        DestinationRegistry.invalidate_status_cache()
        with patch.object(YouTubeMusicDestination, "test_connection", return_value={"connected": True, "message": "YT OK"}) as mock_yt, \
             patch.object(DeezerDestination, "test_connection", return_value={"connected": True, "message": "DZ OK"}) as mock_dz:
            
            # First call: hits endpoints
            res1 = DestinationRegistry.get_all_statuses()
            assert res1["youtube"]["connected"] is True
            assert mock_yt.call_count == 1
            assert mock_dz.call_count == 1

            # Second call within 30s: hits cache, does not make network calls
            res2 = DestinationRegistry.get_all_statuses()
            assert mock_yt.call_count == 1
            assert mock_dz.call_count == 1

            # Force refresh: bypasses cache
            res3 = DestinationRegistry.get_all_statuses(force_refresh=True)
            assert mock_yt.call_count == 2
            assert mock_dz.call_count == 2

    def test_deezer_proxy_session_configuration(self):
        client = DeezerClient(proxy="http://user:pass@1.2.3.4:5678")
        assert client.session.proxies.get("http") == "http://user:pass@1.2.3.4:5678"
        assert client.session.proxies.get("https") == "http://user:pass@1.2.3.4:5678"

    def test_proxy_presets_contain_uk_and_germany(self):
        from src.core.config import DEFAULT_PROXY_PRESETS
        preset_countries = [p["country"] for p in DEFAULT_PROXY_PRESETS]
        assert "GB" in preset_countries
        assert "DE" in preset_countries
        assert len(DEFAULT_PROXY_PRESETS) >= 2

    def test_deezer_get_arl_reads_saved_cookie(self):
        with patch("src.core.config.AppConfig.get_settings", return_value={"deezer": {"arl": "cookie_abc_123"}}):
            assert DeezerAuthManager.get_arl() == "cookie_abc_123"

    def test_deezer_destination_library_playlists(self):
        dest = DeezerDestination(access_token="fake_token")
        assert dest.is_available() is True
        with patch.object(dest.client, "get_user_playlists", return_value=[{"id": 12345, "title": "My Deezer PL", "nb_tracks": 10}]):
            lib = dest.get_library_playlists()
            assert len(lib) == 1
            assert lib[0]["playlistId"] == "12345"
            assert lib[0]["title"] == "My Deezer PL"

    def test_link_existing_mirror_with_deezer(self):
        from src.services.mirror_service import MirrorService
        from src.core.models import Playlist, Track
        mock_dz = MagicMock(spec=DeezerDestination)
        mock_dz.__class__.__name__ = "DeezerDestination"
        mock_dz.get_playlist.return_value = Playlist(
            id="dz_999", name="Test Diff", track_count=1,
            tracks=[Track(id="t1", uri="deezer:track:t1", name="Song 1", artist="Artist 1")]
        )
        with patch("src.services.mirror_service.UnifiedSpotifyProvider") as mock_sp:
            mock_sp_inst = MagicMock()
            mock_sp_inst.get_playlist_tracks.return_value = Playlist(
                id="sp_999", name="Test Diff", track_count=1,
                tracks=[Track(id="t1", uri="spotify:track:t1", name="Song 1", artist="Artist 1")]
            )
            mock_sp.return_value = mock_sp_inst
            mirror = MirrorService.link_existing_mirror(
                spotify_identifier="Test Diff",
                yt_playlist_id="dz_999",
                destination=mock_dz,
                destination_name="deezer"
            )
            assert mirror.destination == "deezer"
            assert mirror.yt_playlist_id == "dz_999"

    def test_destination_scoped_match_cache(self):
        from src.core.database import DatabaseManager
        # Save a match for YouTube
        DatabaseManager.save_cached_match(
            query="test artist unique song",
            video_id="yt_vid_12345",
            destination="youtube"
        )
        # Save a match for Deezer
        DatabaseManager.save_cached_match(
            query="test artist unique song",
            video_id="987654321",
            destination="deezer"
        )

        yt_cached = DatabaseManager.get_cached_match("test artist unique song", destination="youtube")
        dz_cached = DatabaseManager.get_cached_match("test artist unique song", destination="deezer")

        assert yt_cached is not None
        assert yt_cached["video_id"] == "yt_vid_12345"
        assert dz_cached is not None
        assert dz_cached["video_id"] == "987654321"

    def test_deezer_add_tracks_handles_duplicates_and_non_numeric(self):
        client = DeezerClient(arl="dummy_arl")
        client.csrf_token = "fake_csrf"

        # Non-numeric IDs must be discarded
        added = client.add_tracks_to_playlist("123", ["non_numeric_id", "bad_yt_vid"])
        assert added == 0

        # When batch returns ERROR_DATA_EXISTS, fallback to individual addition
        batch_err = MagicMock()
        batch_err.json.return_value = {"error": {"ERROR_DATA_EXISTS": "This song already exists in this playlist"}, "results": {}}
        single_ok = MagicMock()
        single_ok.json.return_value = {"error": [], "results": True}

        with patch.object(client.session, "post", side_effect=[batch_err, single_ok]):
            added = client.add_tracks_to_playlist("123", ["111222"])
            assert added == 1

    def test_deezer_gw_light_pagination_multi_page(self):
        """Verify that gw-light paginates via playlist.getSongs when NB_SONG > initial chunk."""
        client = DeezerClient(arl="dummy_arl")
        client.csrf_token = "fake_csrf"

        page_playlist_resp = MagicMock()
        page_playlist_resp.json.return_value = {
            "results": {
                "DATA": {"PLAYLIST_ID": 999, "TITLE": "Big List", "NB_SONG": 120},
                "SONGS": {"data": [{"SNG_ID": i, "SNG_TITLE": f"Track {i}"} for i in range(100)]}
            }
        }
        get_songs_resp = MagicMock()
        get_songs_resp.json.return_value = {
            "results": {
                "data": [{"SNG_ID": i, "SNG_TITLE": f"Track {i}"} for i in range(100, 120)]
            }
        }

        with patch.object(client, "request", side_effect=DeezerAPIError("REST failed")), \
             patch.object(client.session, "post", side_effect=[page_playlist_resp, get_songs_resp]):
            pl = client.get_playlist("999")
            assert pl is not None
            assert pl["nb_tracks"] == 120
            assert len(pl["tracks"]["data"]) == 120
            assert pl["tracks"]["data"][0]["SNG_TITLE"] == "Track 0"
            assert pl["tracks"]["data"][119]["SNG_TITLE"] == "Track 119"

    def test_deezer_destination_preserves_server_nb_tracks(self):
        """Verify DeezerDestination does not overwrite nb_tracks with len(tracks) if server reports more."""
        dest = DeezerDestination(access_token="test_token")
        raw_pl = {
            "id": "123",
            "title": "Server Count Test",
            "nb_tracks": 250
        }
        # Emulate partial tracks returned
        raw_tracks = [{"id": i, "title": f"Song {i}"} for i in range(50)]

        with patch.object(dest.client, "get_playlist", return_value=raw_pl), \
             patch.object(dest.client, "get_all_playlist_tracks", return_value=raw_tracks):
            pl = dest.get_playlist("123")
            assert pl is not None
            assert pl.track_count == 250
            assert len(pl.tracks) == 50

    def test_deezer_remove_tracks_via_arl(self):
        """Verify remove_tracks_from_playlist invokes playlist.deleteSongs via ARL session."""
        client = DeezerClient(arl="dummy_arl")
        client.csrf_token = "fake_csrf"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": True}

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            removed = client.remove_tracks_from_playlist("555", ["101", "102"])
            assert removed == 2
            assert mock_post.called
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["playlist_id"] == 555
            assert call_kwargs["json"]["songs"] == [[101, 0], [102, 0]]

    def test_deezer_add_tracks_deduplication(self):
        """Verify add_tracks_to_playlist deduplicates repeated IDs in the batch preserving order."""
        client = DeezerClient(arl="dummy_arl")
        client.csrf_token = "fake_csrf"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": True}

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            # Pass duplicate IDs: 101, 102, 101, 103, 102
            added = client.add_tracks_to_playlist("777", ["101", "102", "101", "103", "102"])
            assert added == 3
            assert mock_post.called
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["playlist_id"] == 777
            assert call_kwargs["json"]["songs"] == [["101", 0], ["102", 0], ["103", 0]]

    def test_deezer_multi_artist_search_fallback(self):
        """Verify search_candidates tests individual artists for multi-artist tracks."""
        dest = DeezerDestination(access_token="fake_token")
        track = Track(
            name="Ave Maria",
            artist="Franz Schubert, Renée Fleming, Royal Philharmonic Orchestra",
            duration_seconds=360
        )

        with patch.object(dest.client, "search_advanced", return_value=[{
            "id": 123456,
            "title": "Ave Maria",
            "artist": {"name": "Renée Fleming"},
            "album": {"title": "Sacred Songs"},
            "duration": 362
        }]) as mock_search:
            cands = dest.search_candidates(track, limit=3)
            assert len(cands) > 0
            assert cands[0].artist == "Renée Fleming"
            assert mock_search.called
