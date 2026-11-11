import asyncio
import sys
from pathlib import Path

import pytest

from streamrip import integrity
from streamrip.integrity import FlacIntegrityError, validate_flac


@pytest.mark.parametrize("bits,channels", [(16, 1), (16, 2), (24, 2)])
async def test_repair_matches_encoder_and_changes_only_md5(make_flac, bits, channels):
    path, digest, size = make_flac(bits, channels)
    before = path.read_bytes()
    result = await validate_flac(path, expected_duration=0.1)
    after = path.read_bytes()
    assert result.repaired
    assert result.md5 == digest.hex()
    assert result.decoded_bytes == size
    assert result.duration == pytest.approx(0.1)
    assert after[:26] == before[:26]
    assert after[26:42] == digest
    assert after[42:] == before[42:]
    process = await asyncio.create_subprocess_exec(
        "flac", "--test", "--silent", str(path)
    )
    assert await process.wait() == 0


async def test_existing_valid_checksum_is_unchanged(make_flac):
    path, _, _ = make_flac(unset=False)
    before = path.read_bytes()
    assert not (await validate_flac(path)).repaired
    assert path.read_bytes() == before


async def test_validation_can_leave_missing_checksum_unset(make_flac):
    path, digest, _ = make_flac()
    before = path.read_bytes()
    result = await validate_flac(path, repair=False)
    assert not result.repaired
    assert result.md5 == digest.hex()
    assert path.read_bytes() == before


async def test_catalog_duration_mismatch_is_rejected_before_writing(make_flac):
    path, _, _ = make_flac()
    before = path.read_bytes()
    with pytest.raises(FlacIntegrityError, match=r"duration.*catalog"):
        await validate_flac(path, expected_duration=180)
    assert path.read_bytes() == before


async def test_duration_allows_catalog_rounding(make_flac):
    path, _, _ = make_flac()
    assert (await validate_flac(path, expected_duration=1)).repaired


async def test_existing_bad_checksum_is_rejected_with_decoder_diagnostic(make_flac):
    path, _, _ = make_flac()
    with path.open("r+b") as output:
        output.seek(26)
        output.write(b"\xaa" * 16)
    before = path.read_bytes()
    with pytest.raises(FlacIntegrityError, match=r"(?i)md5.*mismatch"):
        await validate_flac(path)
    assert path.read_bytes() == before


async def test_corrupted_frame_is_rejected_without_writing(make_flac):
    path, _, _ = make_flac()
    damaged = bytearray(path.read_bytes())
    damaged[-1] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(FlacIntegrityError, match="decode/integrity"):
        await validate_flac(path)
    assert path.read_bytes() == bytes(damaged)


async def test_invalid_header_is_rejected(tmp_path):
    path = tmp_path / "wrong.flac"
    path.write_bytes(b"not FLAC")
    with pytest.raises(FlacIntegrityError, match="STREAMINFO"):
        await validate_flac(path)


async def test_missing_decoder_is_an_explicit_error(make_flac, monkeypatch):
    path, _, _ = make_flac()

    async def missing(*args, **kwargs):
        raise FileNotFoundError("flac")

    monkeypatch.setattr(integrity.asyncio, "create_subprocess_exec", missing)
    with pytest.raises(FlacIntegrityError, match=r"requires.*flac"):
        await validate_flac(path)
    assert path.read_bytes()[26:42] == bytes(16)


@pytest.mark.parametrize("action", ["timeout", "cancel"])
async def test_stalled_decoder_is_killed_and_reaped(make_flac, monkeypatch, action):
    path, _, _ = make_flac()
    spawned = []
    started = asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await real_spawn(
            sys.executable,
            "-c",
            "import os,time; os.write(2,b'diagnostic'*20000); "
            "os.write(1,b'x'*200000); time.sleep(60)",
            **kwargs,
        )
        spawned.append(process)
        started.set()
        return process

    monkeypatch.setattr(integrity.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        validate_flac(path, timeout=0.2 if action == "timeout" else 10)
    )
    await asyncio.wait_for(started.wait(), 2)
    if action == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    else:
        with pytest.raises(FlacIntegrityError, match="timed out"):
            await asyncio.wait_for(task, 3)
    assert spawned and all(p.returncode is not None for p in spawned)
    assert path.read_bytes()[26:42] == bytes(16)


async def test_cancellation_during_process_creation_reaps_child(make_flac, monkeypatch):
    path, _, _ = make_flac()
    started, release = asyncio.Event(), asyncio.Event()
    spawned = []
    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(60)", **kwargs
        )
        spawned.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(integrity.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(validate_flac(path))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert spawned[0].returncode is not None


async def test_changed_file_is_not_patched(make_flac, monkeypatch):
    path, _, _ = make_flac()
    decode = integrity._decode_pcm

    async def changed(path):
        result = await decode(path)
        path.write_bytes(path.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(integrity, "_decode_pcm", changed)
    with pytest.raises(FlacIntegrityError, match="changed during"):
        await validate_flac(path)
    assert path.read_bytes()[26:42] == bytes(16)


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
async def test_invalid_timeout(budget):
    with pytest.raises(ValueError, match="timeout"):
        await validate_flac(Path("unused.flac"), timeout=budget)


async def test_decoder_diagnostic_is_preserved_and_bounded(make_flac, monkeypatch):
    path, _, _ = make_flac()
    real_spawn = asyncio.create_subprocess_exec

    async def failed(*args, **kwargs):
        return await real_spawn(
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('x'*100000+' FINAL_DIAGNOSTIC'); sys.exit(1)",
            **kwargs,
        )

    monkeypatch.setattr(integrity.asyncio, "create_subprocess_exec", failed)
    with pytest.raises(FlacIntegrityError, match="FINAL_DIAGNOSTIC") as caught:
        await validate_flac(path)
    assert len(str(caught.value)) < 4400
    assert path.read_bytes()[26:42] == bytes(16)
