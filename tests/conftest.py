import hashlib
import shutil
import subprocess

import pytest
from mutagen.flac import FLAC


@pytest.fixture
def make_flac(tmp_path):
    def make(bits=16, channels=2, unset=True, sample_rate=48000):
        if not shutil.which("flac"):
            pytest.skip("flac executable is required for real audio tests")
        raw = tmp_path / f"source-{bits}-{channels}-{sample_rate}.raw"
        path = tmp_path / f"source-{bits}-{channels}-{sample_rate}.flac"
        # Include positive and negative samples in each byte width.
        values = [-123, 456, 0, 12345, -23456] * (960 * channels)
        pcm = b"".join(n.to_bytes(bits // 8, "little", signed=True) for n in values)
        raw.write_bytes(pcm)
        subprocess.run(
            [
                "flac",
                "--silent",
                "--force",
                "--force-raw-format",
                "--endian=little",
                "--sign=signed",
                f"--bps={bits}",
                f"--channels={channels}",
                f"--sample-rate={sample_rate}",
                "-o",
                str(path),
                str(raw),
            ],
            check=True,
        )
        digest = hashlib.md5(pcm, usedforsecurity=False).digest()
        assert FLAC(path).info.md5_signature.to_bytes(16, "big") == digest
        if unset:
            with path.open("r+b") as output:
                output.seek(26)
                output.write(bytes(16))
        return path, digest, len(pcm)

    return make
