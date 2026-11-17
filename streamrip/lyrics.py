"""Qobuz lyrics parsing and sidecars; no dependency on audio downloading."""

import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lyrics:
    plain: str
    synced: str = ""

    def text(self, prefer_synced: bool) -> str:
        return (self.synced if prefer_synced else "") or self.plain


def parse_qobuz_lyrics(document: dict) -> Lyrics | None:
    original = document.get("original")
    if not isinstance(original, dict) or not isinstance(original.get("lines"), list):
        return None
    plain, synced = [], []
    has_timed_text = False
    for entry in original["lines"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("line"), str):
            continue
        line = " ".join(entry["line"].splitlines()).strip()
        plain.append(line)
        start = entry.get("start")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or not math.isfinite(start)
            or start < 0
        ):
            continue
        minutes, rest = divmod(int(start) // 10, 6000)
        seconds, centiseconds = divmod(rest, 100)
        synced.append(f"[{minutes:02}:{seconds:02}.{centiseconds:02}]{line}")
        has_timed_text |= bool(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(plain)).strip()
    if not text:
        return None
    return Lyrics(text, "\n".join(synced) if has_timed_text else "")


def write_sidecar(
    audio_path: str, lyrics: Lyrics, prefer_synced: bool, overwrite: bool
) -> Path | None:
    """Publish a complete UTF-8 sidecar; preserve existing files by default."""
    use_synced = prefer_synced and bool(lyrics.synced)
    target = Path(audio_path).with_suffix(".lrc" if use_synced else ".txt")
    if not overwrite and target.exists():
        return None
    fd, tmp = tempfile.mkstemp(prefix=".streamrip-lyrics-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(lyrics.text(prefer_synced) + "\n")
        if overwrite:
            os.replace(tmp, target)
        else:
            try:
                os.link(tmp, target)
            except FileExistsError:
                return None
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target
