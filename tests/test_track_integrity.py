import asyncio
import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mutagen.flac import FLAC

from streamrip.config import Config, ConfigData
from streamrip.integrity import FlacIntegrityError
from streamrip.media import track as track_module
from streamrip.media.track import Track
from streamrip.metadata import AlbumMetadata, TrackMetadata


@pytest.fixture
def track(make_flac, tmp_path):
    source, _, _ = make_flac()
    with open("tests/qobuz_track_resp.json") as f:
        raw = json.load(f)
    raw = copy.deepcopy(raw)
    raw["duration"] = 0.1
    meta = TrackMetadata.from_qobuz(AlbumMetadata.from_qobuz(raw["album"]), raw)
    config = Config.defaults()
    config.session.cli.progress_bars = False
    config.session.downloads.max_connections = 6

    async def download(path, callback):
        shutil.copy(source, path)

    downloadable = SimpleNamespace(
        source="qobuz",
        extension="flac",
        size=AsyncMock(return_value=source.stat().st_size),
        download=AsyncMock(side_effect=download),
    )
    return Track(
        meta, downloadable, config, str(tmp_path / "download"), None, MagicMock()
    )


async def test_track_validates_before_tagging_conversion_and_history(
    track, monkeypatch
):
    order = []
    validate = track_module.validate_flac

    async def checked(path, **kwargs):
        assert not track.db.set_downloaded.called
        result = await validate(path, **kwargs)
        assert result.repaired
        order.append("validate")
        return result

    async def tag(path, *args, **kwargs):
        assert FLAC(path).info.md5_signature
        assert not track.db.set_downloaded.called
        order.append("tag")

    async def convert():
        assert not track.db.set_downloaded.called
        order.append("convert")

    monkeypatch.setattr(track_module, "validate_flac", checked)
    monkeypatch.setattr(track_module, "tag_file", tag)
    monkeypatch.setattr(Track, "_convert", AsyncMock(side_effect=convert))
    track.config.session.conversion.enabled = True
    await track.rip()
    assert order == ["validate", "tag", "convert"]
    track.db.set_downloaded.assert_called_once_with(track.meta.info.id)
    track.db.set_failed.assert_not_called()


async def test_invalid_duration_never_tags_or_marks_downloaded(track, monkeypatch):
    track.meta.info.duration = 180
    tag = AsyncMock()
    monkeypatch.setattr(track_module, "tag_file", tag)
    with pytest.raises(FlacIntegrityError, match="duration"):
        await track.rip()
    assert track.downloadable.download.await_count == 2
    track.db.set_downloaded.assert_not_called()
    track.db.set_failed.assert_called_once_with("qobuz", "track", track.meta.info.id)
    tag.assert_not_awaited()
    assert not await asyncio.to_thread(Path(track.download_path).exists)


async def test_transport_failure_never_reaches_postprocessing(track, monkeypatch):
    track.downloadable.download.side_effect = OSError("connection failed")
    tag = AsyncMock()
    monkeypatch.setattr(track_module, "tag_file", tag)
    with pytest.raises(OSError, match="connection failed"):
        await track.rip()
    assert track.downloadable.download.await_count == 2
    tag.assert_not_awaited()
    track.db.set_downloaded.assert_not_called()


@pytest.mark.parametrize("source,enabled", [("qobuz", False), ("deezer", True)])
async def test_other_sources_and_opt_out_skip_flac_validation(
    track, monkeypatch, source, enabled
):
    track.downloadable.source = source
    track.config.session.qobuz.verify_flac = enabled
    validate, tag = AsyncMock(), AsyncMock()
    monkeypatch.setattr(track_module, "validate_flac", validate)
    monkeypatch.setattr(track_module, "tag_file", tag)
    await track.rip()
    validate.assert_not_awaited()
    tag.assert_awaited_once()
    track.db.set_downloaded.assert_called_once()


async def test_missing_decoder_fails_before_download(track, monkeypatch):
    monkeypatch.setattr(track_module.shutil, "which", lambda name: None)
    with pytest.raises(FlacIntegrityError, match="requires"):
        await track.rip()
    track.downloadable.download.assert_not_awaited()
    track.db.set_downloaded.assert_not_called()


def test_old_config_gets_validation_defaults_without_migration():
    old = ConfigData.from_toml(open("tests/test_config.toml").read())
    assert old.qobuz.verify_flac is True
    assert old.qobuz.flac_validation_timeout == 1800


async def test_invalid_first_download_can_retry_successfully(track):
    download = track.downloadable.download.side_effect
    attempt = 0

    async def transient(path, callback):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            await asyncio.to_thread(Path(path).write_bytes, b"incomplete FLAC")
        else:
            await download(path, callback)

    track.downloadable.download.side_effect = transient
    await track.rip()
    assert attempt == 2
    assert FLAC(track.download_path).info.md5_signature
    track.db.set_failed.assert_not_called()
    track.db.set_downloaded.assert_called_once()


async def test_cancelled_validation_cleans_partial_track_and_history(
    track, monkeypatch
):
    entered = asyncio.Event()

    async def validating(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(track_module, "validate_flac", validating)
    task = asyncio.create_task(track.rip())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert not await asyncio.to_thread(Path(track.download_path).exists)
    track.db.set_downloaded.assert_not_called()


async def test_single_track_progress_is_removed_on_failure(track, monkeypatch):
    track.is_single = True
    add, remove = MagicMock(), MagicMock()
    monkeypatch.setattr(track_module, "add_title", add)
    monkeypatch.setattr(track_module, "remove_title", remove)
    monkeypatch.setattr(track_module.shutil, "which", lambda name: None)
    with pytest.raises(FlacIntegrityError):
        await track.rip()
    add.assert_called_once_with(track.meta.title)
    remove.assert_called_once_with(track.meta.title)
