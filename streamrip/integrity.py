"""Validate FLAC audio and fill missing PCM checksums without re-encoding."""

import asyncio
import hashlib
import math
import os
from dataclasses import dataclass
from pathlib import Path

from mutagen.flac import FLAC


class FlacIntegrityError(ValueError):
    """A FLAC cannot be validated safely."""


@dataclass(frozen=True)
class FlacValidation:
    md5: str
    decoded_bytes: int
    duration: float
    repaired: bool


def _read_info(path):
    with path.open("rb") as source:
        header = source.read(42)
    if (
        len(header) != 42
        or header[:4] != b"fLaC"
        or header[4] & 127 != 0
        or int.from_bytes(header[5:8], "big") != 34
    ):
        raise FlacIntegrityError(f"Invalid FLAC STREAMINFO: {path.name}")
    info = FLAC(path).info
    if info.total_samples <= 0:
        raise FlacIntegrityError(f"Unknown FLAC sample count: {path.name}")
    return info, header, path.stat()


def _patch_md5(path, original_header, snapshot, digest):
    with path.open("r+b") as output:
        current = os.fstat(output.fileno())
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
            snapshot.st_dev,
            snapshot.st_ino,
            snapshot.st_size,
            snapshot.st_mtime_ns,
        ) or output.read(42) != original_header:
            raise FlacIntegrityError(f"FLAC changed during validation: {path.name}")
        output.seek(26)
        if output.write(digest) != 16:
            raise FlacIntegrityError(f"Incomplete FLAC checksum write: {path.name}")
        output.flush()
        os.fsync(output.fileno())
        output.seek(26)
        if output.read(16) != digest:
            raise FlacIntegrityError(f"FLAC checksum readback failed: {path.name}")


async def _stderr_tail(stream):
    tail = b""
    while chunk := await stream.read(4096):
        tail = (tail + chunk)[-4096:]
    return tail.decode("utf-8", errors="replace").strip()


async def _drain(stream):
    while await stream.read(65536):
        pass


async def _cleanup_decoder(process, stderr_task):
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    # Drain stdout even on cancellation: Process.wait can otherwise hang when
    # a subprocess pipe is full. Stderr has its own bounded reader.
    await asyncio.gather(
        _drain(process.stdout), stderr_task, process.wait(), return_exceptions=True
    )


async def _decode_pcm(path):
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            "flac",
            "--decode",
            "--stdout",
            "--silent",
            "--force-raw-format",
            "--endian=little",
            "--sign=signed",
            str(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # Cancellation during process creation must not leave an orphan decoder.
        try:
            process = await spawn
        except OSError:
            raise asyncio.CancelledError from None
        stderr_task = asyncio.create_task(_stderr_tail(process.stderr))
        await _cleanup_decoder(process, stderr_task)
        raise
    except FileNotFoundError as exc:
        raise FlacIntegrityError(
            "FLAC validation requires the 'flac' executable"
        ) from exc
    stderr_task = asyncio.create_task(_stderr_tail(process.stderr))
    digest, size = hashlib.md5(usedforsecurity=False), 0
    try:
        while chunk := await process.stdout.read(65536):
            digest.update(chunk)
            size += len(chunk)
        await process.wait()
        diagnostic = await stderr_task
        if process.returncode:
            raise FlacIntegrityError(
                f"FLAC decode/integrity check failed for {path.name}: "
                f"{diagnostic or 'decoder exited with code ' + str(process.returncode)}"
            )
        return digest.digest(), size
    finally:
        cleanup = asyncio.create_task(_cleanup_decoder(process, stderr_task))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


async def validate_flac(
    path: str | Path,
    *,
    expected_duration: float | None = None,
    timeout: float = 1800,  # noqa: ASYNC109 - caller budget; supports Python 3.10.
    duration_tolerance: float = 3,
    repair: bool = True,
) -> FlacValidation:
    """Decode and verify a FLAC, optionally filling only an unset STREAMINFO MD5.

    Existing nonzero checksums are always verified, never overwritten. The
    timeout covers inspection and decoding; cancellation kills and reaps the
    decoder. A short checksum write is allowed to finish before cancellation
    returns, so a background thread cannot modify the file after the caller exits.
    """
    path = Path(path)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("FLAC validation timeout must be positive and finite")
    if not math.isfinite(duration_tolerance) or duration_tolerance < 0:
        raise ValueError("Duration tolerance must be nonnegative and finite")
    if expected_duration is not None and (
        isinstance(expected_duration, bool)
        or not math.isfinite(expected_duration)
        or expected_duration <= 0
    ):
        raise ValueError("Expected duration must be positive and finite")

    async def validate():
        info, header, snapshot = await asyncio.to_thread(_read_info, path)
        duration = info.total_samples / info.sample_rate
        if (
            expected_duration is not None
            and abs(duration - expected_duration) > duration_tolerance
        ):
            raise FlacIntegrityError(
                f"FLAC duration {duration:.3f}s differs from catalog "
                f"{expected_duration:.3f}s: {path.name}"
            )
        digest, size = await _decode_pcm(path)
        expected_bytes = (
            info.total_samples * info.channels * ((info.bits_per_sample + 7) // 8)
        )
        if size != expected_bytes:
            raise FlacIntegrityError(
                f"Decoded audio length does not match STREAMINFO: {path.name}"
            )
        original = header[26:42]
        if original != bytes(16) and original != digest:
            raise FlacIntegrityError(f"FLAC STREAMINFO MD5 mismatch: {path.name}")
        repaired = original == bytes(16) and repair
        if repaired:
            write = asyncio.create_task(
                asyncio.to_thread(_patch_md5, path, header, snapshot, digest)
            )
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                raise
        return FlacValidation(digest.hex(), size, duration, repaired)

    try:
        return await asyncio.wait_for(validate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise FlacIntegrityError(
            f"FLAC validation timed out after {timeout:g}s: {path.name}"
        ) from exc
