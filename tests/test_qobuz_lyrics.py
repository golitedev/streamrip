import asyncio
import contextlib
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aiohttp import web
from mutagen.flac import FLAC

from streamrip.client import QobuzClient
from streamrip.client import qobuz as qobuz_module
from streamrip.config import Config, ConfigData
from streamrip.lyrics import Lyrics, parse_qobuz_lyrics, write_sidecar
from streamrip.media.track import Track
from streamrip.metadata import AlbumMetadata, TrackMetadata


@pytest.fixture
def config():
    return Config("tests/test_config.toml")


def test_old_configs_default_to_disabled_and_roundtrip(config):
    q = config.session.qobuz
    assert not q.download_lyrics
    assert q.lyrics_mode == "sidecar"
    config.file.qobuz.download_lyrics = True
    config.file.qobuz.lyrics_mode = "both"
    config.file.update_toml()
    restored = ConfigData.from_toml(config.file.toml.as_string()).qobuz
    assert restored.download_lyrics and restored.lyrics_mode == "both"


@pytest.mark.parametrize(
    "key,value",
    [
        ("lyrics_mode", "invalid"),
        ("download_lyrics", "yes"),
        ("lyrics_synced", 1),
        ("lyrics_overwrite", "false"),
    ],
)
def test_bad_config_is_rejected(config, key, value):
    config.file.toml["qobuz"][key] = value
    with pytest.raises(ValueError, match=key):
        ConfigData.from_toml(config.file.toml.as_string())


def test_parser_preserves_verses_and_zero_timestamps():
    result = parse_qobuz_lyrics(
        {
            "original": {
                "type": "wsync",
                "lines": [
                    {"line": "Primera línea", "start": 0, "end": 1000},
                    {"line": ""},
                    {"line": ""},
                    {"line": ""},
                    {"line": "Otra línea", "start": 61234},
                    {"line": "", "start": 65000},
                    {"line": "Untimed line"},
                ],
            }
        }
    )
    assert result.plain == "Primera línea\n\nOtra línea\n\nUntimed line"
    assert result.synced == "[00:00.00]Primera línea\n[01:01.23]Otra línea\n[01:05.00]"


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"original": None},
        {"original": {"lines": "invalid"}},
        {"original": {"lines": [{"line": ""}]}},
    ],
)
def test_empty_documents(document):
    assert parse_qobuz_lyrics(document) is None


def test_invalid_timestamps_fall_back_to_plain():
    result = parse_qobuz_lyrics(
        {
            "original": {
                "lines": [
                    {"line": "Text", "start": -1},
                    {"line": "More", "start": True},
                    {"line": "End", "start": float("nan")},
                    {"line": None},
                ]
            }
        }
    )
    assert result == Lyrics("Text\nMore\nEnd")


def test_sidecar_utf8_fallback_and_overwrite(tmp_path):
    audio = str(tmp_path / "01 - Title.flac")
    lyrics = Lyrics("Canción", "[00:00.00]Canción")
    lrc = write_sidecar(audio, lyrics, True, False)
    assert lrc.read_text() == "[00:00.00]Canción\n"
    lrc.write_text("existing lyrics")
    assert write_sidecar(audio, lyrics, True, False) is None
    assert lrc.read_text() == "existing lyrics"
    write_sidecar(audio, lyrics, True, True)
    assert lrc.read_text() == "[00:00.00]Canción\n"
    txt = write_sidecar(audio, lyrics, False, False)
    assert txt.suffix == ".txt" and txt.read_text() == "Canción\n"
    fallback = write_sidecar(str(tmp_path / "Other.mp3"), Lyrics("Plain"), True, False)
    assert fallback.suffix == ".txt"
    assert not list(tmp_path.glob(".streamrip-lyrics-*"))


@pytest.mark.parametrize(
    "api_status,cdn_status,available",
    [
        (200, 200, True),
        (404, 200, False),
        (200, 403, False),
        (200, 404, False),
        (403, 200, False),
        (500, 200, None),
        (200, 500, None),
    ],
)
async def test_signed_api_and_cdn_do_not_share_credentials(
    config, monkeypatch, api_status, cdn_status, available
):
    seen = []
    base = ""

    async def api(request):
        seen.append("api")
        assert request.headers["X-User-Auth-Token"] == "account-token"
        params = request.query
        assert params["track_id"] == "123"
        assert "language" not in params
        raw = "tracklyricsUrltrack_id123" + params["request_ts"] + "test-secret"
        assert params["request_sig"] == hashlib.md5(raw.encode()).hexdigest()
        return web.json_response({"lyrics_url": base + "/lyrics"}, status=api_status)

    async def cdn(request):
        seen.append("cdn")
        assert "X-User-Auth-Token" not in request.headers
        assert "X-App-Id" not in request.headers
        return web.json_response(
            {"original": {"lines": [{"line": "Hello", "start": 1000}]}},
            status=cdn_status,
        )

    app = web.Application()
    app.router.add_get("/track/lyricsUrl", api)
    app.router.add_get("/lyrics", cdn)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    monkeypatch.setattr(qobuz_module, "QOBUZ_BASE_URL", base)
    client = QobuzClient(config)
    client.secret = "test-secret"
    client.logged_in = True
    client.rate_limiter = contextlib.nullcontext()
    try:
        async with aiohttp.ClientSession(
            headers={"X-User-Auth-Token": "account-token", "X-App-Id": "app"}
        ) as session:
            client.session = session
            if available is None:
                with pytest.raises(RuntimeError, match="HTTP 500"):
                    await client.get_lyrics("123")
                result = None
            else:
                result = await client.get_lyrics("123")
        if available is not None:
            assert (result is not None) == available
        assert seen == (["api"] if api_status != 200 else ["api", "cdn"])
        if result:
            assert result == Lyrics("Hello", "[00:01.00]Hello")
    finally:
        await runner.cleanup()


@pytest.fixture
def track(config, tmp_path):
    resp = json.loads(Path("tests/qobuz_track_resp.json").read_text())
    meta = TrackMetadata.from_qobuz(AlbumMetadata.from_qobuz(resp["album"]), resp)
    path = tmp_path / "01 - Track.flac"
    shutil.copy("tests/silence.flac", path)
    audio = FLAC(path)
    audio.delete()
    config.session.qobuz.download_lyrics = True
    config.session.conversion.enabled = False
    config.session.metadata.exclude = []
    client = SimpleNamespace(
        get_lyrics=AsyncMock(return_value=Lyrics("Hello", "[00:01.00]Hello"))
    )
    return Track(
        meta,
        SimpleNamespace(source="qobuz"),
        config,
        str(tmp_path),
        None,
        Mock(),
        download_path=str(path),
        lyrics_client=client,
    )


@pytest.mark.parametrize("mode", ["sidecar", "embedded", "both"])
@pytest.mark.parametrize("synced", [True, False])
async def test_modes_tag_real_flac_and_write_matching_sidecar(track, mode, synced):
    q = track.config.session.qobuz
    q.lyrics_mode = mode
    q.lyrics_synced = synced
    checksum = FLAC(track.download_path).info.md5_signature
    await track.postprocess()
    audio = FLAC(track.download_path)
    text = "[00:01.00]Hello" if synced else "Hello"
    if mode in {"embedded", "both"}:
        assert audio["lyrics"] == [text]
    else:
        assert "lyrics" not in audio
    sidecar = Path(track.download_path).with_suffix(".lrc" if synced else ".txt")
    assert sidecar.exists() == (mode in {"sidecar", "both"})
    if sidecar.exists():
        assert sidecar.read_text() == text + "\n"
    assert audio.info.md5_signature == checksum
    track.db.set_downloaded.assert_called_once_with(track.meta.info.id)


@pytest.mark.parametrize(
    "reason",
    ["disabled", "excluded", "other_source", "missing_client", "missing", "failure"],
)
async def test_no_lyrics_never_breaks_audio(track, reason):
    client = track.lyrics_client
    if reason == "disabled":
        track.config.session.qobuz.download_lyrics = False
    elif reason == "excluded":
        track.config.session.metadata.exclude = ["LYRICS"]
    elif reason == "other_source":
        track.downloadable.source = "tidal"
    elif reason == "missing_client":
        track.lyrics_client = None
    elif reason == "missing":
        client.get_lyrics.return_value = None
    else:
        client.get_lyrics.side_effect = RuntimeError("service unavailable")
    await track.postprocess()
    assert not Path(track.download_path).with_suffix(".lrc").exists()
    assert "lyrics" not in FLAC(track.download_path)
    track.db.set_downloaded.assert_called_once()
    if reason not in {"missing", "failure"}:
        client.get_lyrics.assert_not_awaited()


async def test_cancel_is_not_swallowed(track):
    track.lyrics_client.get_lyrics.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await track.postprocess()
    track.db.set_downloaded.assert_not_called()


async def test_sidecar_uses_final_converted_path(track, monkeypatch):
    async def convert(self):
        self.download_path = str(Path(self.download_path).with_suffix(".m4a"))

    monkeypatch.setattr(Track, "_convert", convert)
    track.config.session.conversion.enabled = True
    track.config.session.qobuz.lyrics_mode = "sidecar"
    await track.postprocess()
    assert (
        Path(track.download_path).with_suffix(".lrc").read_text() == "[00:01.00]Hello\n"
    )


async def test_sidecar_write_error_does_not_fail_download(track, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("streamrip.media.track.write_sidecar", fail)
    await track.postprocess()
    track.db.set_downloaded.assert_called_once()


@pytest.mark.parametrize("kind", ["album", "single", "playlist"])
async def test_all_entrypoints_pass_lyrics_client(config, tmp_path, monkeypatch, kind):
    from streamrip.media.playlist import PendingPlaylistTrack
    from streamrip.media.track import PendingSingle, PendingTrack

    resp = json.loads(
        await asyncio.to_thread(Path("tests/qobuz_track_resp.json").read_text)
    )
    client = QobuzClient(config)
    client.get_metadata = AsyncMock(return_value=resp)
    client.get_downloadable = AsyncMock(return_value=SimpleNamespace(source="qobuz"))
    db = Mock()
    db.downloaded.return_value = False
    config.session.downloads.folder = str(tmp_path)
    monkeypatch.setattr(PendingSingle, "_download_cover", AsyncMock(return_value=None))
    monkeypatch.setattr(
        PendingPlaylistTrack, "_download_cover", AsyncMock(return_value=None)
    )
    if kind == "album":
        pending = PendingTrack(
            "123",
            AlbumMetadata.from_qobuz(resp["album"]),
            client,
            config,
            str(tmp_path),
            db,
            None,
        )
    elif kind == "single":
        pending = PendingSingle("123", client, config, db)
    else:
        pending = PendingPlaylistTrack(
            "123", client, config, str(tmp_path), "Playlist", 1, db
        )
    track = await pending.resolve()
    assert track is not None and track.lyrics_client is client
    client.get_metadata.assert_awaited_once_with("123", "track")
