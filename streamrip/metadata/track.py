from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .album import AlbumMetadata
from .qobuz import (
    artist_credit,
    contributor_credits,
    replaygain_tags,
    structured_artists,
    text,
    unique_names,
    with_version,
)
from .util import safe_get, typed

logger = logging.getLogger("streamrip")


@dataclass(slots=True)
class TrackInfo:
    id: str
    quality: int

    bit_depth: Optional[int] = None
    explicit: bool = False
    sampling_rate: Optional[int | float] = None
    work: Optional[str] = None


@dataclass(slots=True)
class TrackMetadata:
    info: TrackInfo

    title: str
    album: AlbumMetadata
    artist: str
    tracknumber: int
    discnumber: int
    composer: str | None
    isrc: str | None = None
    lyrics: str | None = ""
    artists: list[str] = field(default_factory=list)
    credits: dict[str, list[str]] = field(default_factory=dict)
    qobuz_track_id: str | None = None
    itunesadvisory: str | None = None
    copyright: str | None = None
    replaygain: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_qobuz(cls, album: AlbumMetadata, resp: dict) -> TrackMetadata | None:
        title = typed(resp["title"].strip(), str)
        isrc = text(resp.get("isrc"))
        streamable = typed(resp.get("streamable", False), bool)

        if not streamable:
            return None

        title = with_version(title, resp.get("version"))
        work = text(resp.get("work"))
        if work is not None and work not in title:
            title = f"{work}: {title}"

        composer = text(safe_get(resp, "composer", "name"))
        tracknumber = typed(resp.get("track_number", 1), int)
        discnumber = typed(resp.get("media_number", 1), int)
        primary = text(safe_get(resp, "performer", "name"))
        main, featured = structured_artists(resp.get("artists"))
        credits = contributor_credits(
            resp.get("performers"),
            [primary, composer, *album.albumartists, *main, *featured],
            artist_names=[primary, *album.albumartists, *main, *featured],
        )
        parsed_main = credits.pop("main", [])
        parsed_featured = credits.pop("featured", [])
        main = main or parsed_main
        featured = featured or parsed_featured
        if (
            not main
            and primary
            and primary.casefold() not in {n.casefold() for n in featured}
        ):
            main = [primary]
        artists = unique_names([*main, *featured])
        artist = artist_credit(main, featured) or primary or album.albumartist
        if not credits.get("composer") and composer:
            credits["composer"] = [composer]
        composer = ", ".join(credits.get("composer", [])) or None
        track_id = str(resp["id"])
        bit_depth = typed(resp.get("maximum_bit_depth"), int | None)
        sampling_rate = typed(resp.get("maximum_sampling_rate"), int | float | None)
        warning = resp.get("parental_warning")
        explicit = warning is True

        info = TrackInfo(
            id=track_id,
            quality=album.info.quality,
            bit_depth=bit_depth,
            explicit=explicit,
            sampling_rate=sampling_rate,
            work=work,
        )
        return cls(
            info=info,
            title=title,
            album=album,
            artist=artist,
            tracknumber=tracknumber,
            discnumber=discnumber,
            composer=composer,
            isrc=isrc,
            artists=artists,
            credits=credits,
            qobuz_track_id=track_id,
            # False means no advisory; Qobuz does not establish an edited clean
            # version, so do not invent ITUNESADVISORY=2.
            itunesadvisory="1"
            if warning is True
            else "0"
            if warning is False
            else None,
            copyright=text(resp.get("copyright")),
            replaygain=replaygain_tags(resp.get("audio_info")),
        )

    @classmethod
    def from_deezer(cls, album: AlbumMetadata, resp) -> TrackMetadata | None:
        track_id = str(resp["id"])
        isrc = typed(resp["isrc"], str)
        bit_depth = 16
        sampling_rate = 44.1
        explicit = typed(resp["explicit_lyrics"], bool)
        work = None
        title = typed(resp["title"], str)
        artist = typed(resp["artist"]["name"], str)
        tracknumber = typed(resp["track_position"], int)
        discnumber = typed(resp["disk_number"], int)
        composer = None
        info = TrackInfo(
            id=track_id,
            quality=album.info.quality,
            bit_depth=bit_depth,
            explicit=explicit,
            sampling_rate=sampling_rate,
            work=work,
        )
        return cls(
            info=info,
            title=title,
            album=album,
            artist=artist,
            tracknumber=tracknumber,
            discnumber=discnumber,
            composer=composer,
            isrc=isrc,
        )

    @classmethod
    def from_soundcloud(cls, album: AlbumMetadata, resp: dict) -> TrackMetadata:
        track = resp
        track_id = track["id"]
        isrc = typed(safe_get(track, "publisher_metadata", "isrc"), str | None)
        bit_depth, sampling_rate = None, None
        explicit = typed(
            safe_get(track, "publisher_metadata", "explicit", default=False),
            bool,
        )

        title = typed(track["title"].strip(), str)
        artist = typed(track["user"]["username"], str)
        tracknumber = 1

        info = TrackInfo(
            id=track_id,
            quality=album.info.quality,
            bit_depth=bit_depth,
            explicit=explicit,
            sampling_rate=sampling_rate,
            work=None,
        )
        return cls(
            info=info,
            title=title,
            album=album,
            artist=artist,
            tracknumber=tracknumber,
            discnumber=0,
            composer=None,
            isrc=isrc,
        )

    @classmethod
    def from_tidal(cls, album: AlbumMetadata, track) -> TrackMetadata:
        title = typed(track["title"], str).strip()
        item_id = str(track["id"])
        isrc = typed(track["isrc"], str)
        version = track.get("version")
        explicit = track.get("explicit", False)
        if version:
            title = f"{title} ({version})"

        tracknumber = typed(track.get("trackNumber", 1), int)
        discnumber = typed(track.get("volumeNumber", 1), int)

        artists = track.get("artists")
        if len(artists) > 0:
            artist = ", ".join(a["name"] for a in artists)
        else:
            artist = track["artist"]["name"]

        lyrics = track.get("lyrics", "")

        quality_map: dict[str, int] = {
            "LOW": 0,
            "HIGH": 1,
            "LOSSLESS": 2,
            "HI_RES": 3,
        }

        tidal_quality = track.get("audioQuality")
        if tidal_quality is not None:
            quality = quality_map[tidal_quality]
        else:
            quality = 0

        if quality >= 2:
            sampling_rate = 44100
            if quality == 3:
                bit_depth = 24
            else:
                bit_depth = 16
        else:
            sampling_rate = bit_depth = None

        info = TrackInfo(
            id=item_id,
            quality=quality,
            bit_depth=bit_depth,
            explicit=explicit,
            sampling_rate=sampling_rate,
            work=None,
        )
        return cls(
            info=info,
            title=title,
            album=album,
            artist=artist,
            tracknumber=tracknumber,
            discnumber=discnumber,
            composer=None,
            isrc=isrc,
            lyrics=lyrics,
        )

    @classmethod
    def from_resp(cls, album: AlbumMetadata, source, resp) -> TrackMetadata | None:
        if source == "qobuz":
            return cls.from_qobuz(album, resp)
        if source == "tidal":
            return cls.from_tidal(album, resp)
        if source == "soundcloud":
            return cls.from_soundcloud(album, resp)
        if source == "deezer":
            return cls.from_deezer(album, resp)
        raise Exception

    def format_track_path(self, format_string: str) -> str:
        # Available keys: "tracknumber", "artist", "albumartist", "composer", "title",
        # and "explicit", "albumcomposer"
        none_text = "Unknown"
        info = {
            "id": self.info.id,
            "title": self.title,
            "tracknumber": self.tracknumber,
            "artist": self.artist,
            "albumartist": self.album.albumartist,
            "albumcomposer": self.album.albumcomposer or none_text,
            "composer": self.composer or none_text,
            "explicit": " (Explicit) " if self.info.explicit else "",
        }
        return format_string.format(**info)
