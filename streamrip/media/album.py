import asyncio
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from string import Formatter

import mutagen

from .. import progress
from ..client import Client
from ..config import Config
from ..db import Database
from ..exceptions import NonStreamableError
from ..filepath_utils import clean_filepath
from ..metadata import AlbumMetadata
from ..metadata.util import get_album_track_ids
from .artwork import download_artwork, relocate_artwork_tempdir
from .media import Media, Pending
from .track import PendingTrack

logger = logging.getLogger("streamrip")


def format_album_folder(root: str, meta: AlbumMetadata, config: Config) -> str:
    paths = config.session.filepaths
    return os.path.join(
        root,
        clean_filepath(
            meta.format_folder_path(paths.folder_format), paths.restrict_characters
        ),
    )


@dataclass(slots=True)
class Album(Media):
    meta: AlbumMetadata
    tracks: list[PendingTrack]
    config: Config
    # folder where the tracks will be downloaded
    folder: str
    db: Database
    folder_root: str | None = None
    downloaded_paths: list[str] = field(init=False, default_factory=list)

    async def preprocess(self):
        progress.add_title(self.meta.album)

    async def download(self):
        async def _resolve_and_download(pending: PendingTrack) -> str | None:
            try:
                track = await pending.resolve()
                if track is None:
                    return
                await track.rip()
                return track.download_path
            except Exception as e:
                logger.error(f"Error downloading track: {e}")

        results = await asyncio.gather(
            *[_resolve_and_download(p) for p in self.tracks], return_exceptions=True
        )

        self.downloaded_paths = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Album track processing error: {result}")
            elif isinstance(result, str):
                self.downloaded_paths.append(result)

    async def postprocess(self):
        progress.remove_title(self.meta.album)
        # Keep the original path for incomplete downloads and database skips.
        if (
            self.folder_root is not None
            and self.tracks
            and len(set(self.downloaded_paths)) == len(self.tracks)
        ):
            await asyncio.to_thread(self._finalize_folder)

    def _finalize_folder(self):
        root = self.folder_root
        assert root is not None
        template = self.config.session.filepaths.folder_format
        fields = {name for _, name, _, _ in Formatter().parse(template)}
        if not fields.intersection({"bit_depth", "sampling_rate"}):
            return

        quality = []
        for path in self.downloaded_paths:
            try:
                audio = mutagen.File(path)
            except (OSError, mutagen.MutagenError) as error:
                logger.warning("Cannot read audio quality for %s: %s", path, error)
                return
            rate = getattr(audio.info, "sample_rate", 0) if audio is not None else 0
            if not rate:
                logger.warning("Cannot determine audio quality for %s", path)
                return
            quality.append((getattr(audio.info, "bits_per_sample", 0) or 0, rate))

        # Compare bit depth first and use the sample rate from that same track.
        bits, rate = max(quality)
        rate_khz = rate / 1000 if rate % 1000 else rate // 1000
        info = replace(self.meta.info, bit_depth=bits or None, sampling_rate=rate_khz)
        rendered = format_album_folder(root, replace(self.meta, info=info), self.config)
        source, target = Path(self.folder), Path(rendered)
        if source == target:
            return
        if (
            source in target.parents
            or target in source.parents
            or os.path.lexists(target)
        ):
            logger.warning(
                "Cannot rename album folder to %s; leaving %s", target, source
            )
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        except OSError as error:
            logger.warning("Cannot rename album folder %s: %s", source, error)
            return
        relocate_artwork_tempdir(self.folder, str(target))
        self.folder = str(target)


@dataclass(slots=True)
class PendingAlbum(Pending):
    id: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Album | None:
        try:
            resp = await self.client.get_metadata(self.id, "album")
        except NonStreamableError as e:
            logger.error(
                f"Album {self.id} not available to stream on {self.client.source} ({e})",
            )
            return None

        try:
            meta = AlbumMetadata.from_album_resp(resp, self.client.source)
        except Exception as e:
            logger.error(f"Error building album metadata for {id=}: {e}")
            return None

        if meta is None:
            logger.error(
                f"Album {self.id} not available to stream on {self.client.source}",
            )
            return None

        tracklist = get_album_track_ids(self.client.source, resp)
        downloads = self.config.session.downloads
        folder_root = downloads.folder
        if downloads.source_subdirectories:
            folder_root = os.path.join(folder_root, self.client.source.capitalize())
        album_folder = format_album_folder(folder_root, meta, self.config)
        os.makedirs(album_folder, exist_ok=True)
        embed_cover, _ = await download_artwork(
            self.client.session,
            album_folder,
            meta.covers,
            self.config.session.artwork,
            for_playlist=False,
        )
        pending_tracks = [
            PendingTrack(
                id,
                album=meta,
                client=self.client,
                config=self.config,
                folder=album_folder,
                db=self.db,
                cover_path=embed_cover,
            )
            for id in tracklist
        ]
        logger.debug("Pending tracks: %s", pending_tracks)
        return Album(
            meta, pending_tracks, self.config, album_folder, self.db, folder_root
        )
