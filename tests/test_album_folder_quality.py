import asyncio
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from streamrip.config import Config
from streamrip.media.album import PendingAlbum
from streamrip.media.artwork import remove_artwork_tempdirs


@pytest.fixture
def album(tmp_path, monkeypatch):
    response = json.loads(Path("tests/qobuz_album_resp.json").read_text())
    response.update(
        maximum_bit_depth=24,
        maximum_sampling_rate=192,
        tracks={"items": [{"id": 123}]},
    )
    config = Config("tests/test_config.toml")
    monkeypatch.chdir(tmp_path)
    config.session.downloads.folder = "./downloads"
    config.session.downloads.source_subdirectories = True
    config.session.filepaths.folder_format = "{bit_depth}B-{sampling_rate}kHz/{title}"
    client = SimpleNamespace(
        source="qobuz", session=None, get_metadata=AsyncMock(return_value=response)
    )
    monkeypatch.setattr(
        "streamrip.media.album.download_artwork", AsyncMock(return_value=(None, None))
    )
    result = asyncio.run(PendingAlbum("id", client, config, None).resolve())
    assert result is not None
    assert Path(result.folder).parent.name == "24B-192kHz"
    return result


def tracks(album, make_flac, formats):
    result = []
    for number, (bits, rate) in enumerate(formats, start=1):
        source, _, _ = make_flac(bits=bits, unset=False, sample_rate=rate)
        path = Path(album.folder) / f"Disc {number}" / "track.flac"
        path.parent.mkdir()
        shutil.copyfile(source, path)
        track = SimpleNamespace(download_path=str(path), rip=AsyncMock())
        result.append(SimpleNamespace(resolve=AsyncMock(return_value=track)))
    return result


@pytest.mark.parametrize(
    "formats, expected",
    [
        ([(24, 44100), (16, 192000), (24, 96000)], "24B-96kHz"),
        ([(16, 44100)], "16B-44.1kHz"),
    ],
)
def test_uses_highest_downloaded_track_and_moves_whole_folder(
    album, make_flac, monkeypatch, formats, expected
):
    album.tracks = tracks(album, make_flac, formats)
    source = Path(album.folder)
    (source / "cover.jpg").write_bytes(b"cover")
    (source / "Disc 1" / "track.lrc").write_bytes(b"lyrics")
    temporary_art = source / "__artwork"
    temporary_art.mkdir()
    monkeypatch.setattr(
        "streamrip.media.artwork._artwork_tempdirs",
        {os.path.join(album.folder, "__artwork")},
    )

    asyncio.run(album.rip())
    remove_artwork_tempdirs()

    target = Path(album.folder)
    assert target == source.parent.parent / expected / source.name
    assert not source.exists()
    assert (target / "cover.jpg").read_bytes() == b"cover"
    assert (target / "Disc 1" / "track.lrc").read_bytes() == b"lyrics"
    assert not (target / "__artwork").exists()
    assert len(list(target.rglob("*.flac"))) == len(formats)
    assert album.meta.info.sampling_rate == 192


@pytest.mark.parametrize("skip_reason", ["partial", "collision"])
def test_leaves_unfinished_or_conflicting_downloads_in_place(
    album, make_flac, skip_reason
):
    album.tracks = tracks(album, make_flac, [(24, 96000)])
    source = Path(album.folder)
    target = source.parent.parent / "24B-96kHz" / source.name
    if skip_reason == "partial":
        album.tracks.append(SimpleNamespace(resolve=AsyncMock(return_value=None)))
    else:
        target.mkdir(parents=True)
        (target / "keep").write_bytes(b"existing")

    asyncio.run(album.rip())

    assert Path(album.folder) == source
    assert (source / "Disc 1" / "track.flac").is_file()
    if skip_reason == "collision":
        assert list(target.iterdir()) == [target / "keep"]
    else:
        assert not target.exists()
