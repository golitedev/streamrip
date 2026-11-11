import asyncio
import copy
import json
import shutil

import pytest
from mutagen.flac import FLAC

from streamrip.metadata import AlbumMetadata, TrackMetadata, tag_file
from streamrip.metadata.qobuz import (
    contributor_credits,
    release_date,
    replaygain_tags,
    structured_artists,
    with_version,
)

with open("tests/qobuz_track_resp.json") as f:
    BASE = json.load(f)


def metadata(resp=None):
    resp = copy.deepcopy(BASE if resp is None else resp)
    return TrackMetadata.from_qobuz(AlbumMetadata.from_qobuz(resp["album"]), resp)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2020", "2020"),
        ("2020-02", "2020-02"),
        ("2020-02-29", "2020-02-29"),
        ("2021-02-29", None),
        ("2020-13", None),
        ("", None),
        (None, None),
    ],
)
def test_dates(value, expected):
    assert release_date(value) == expected


@pytest.mark.parametrize(
    "title, version, expected",
    [
        ("Album", "Deluxe", "Album (Deluxe)"),
        ("Album (DELUXE)", "Deluxe", "Album (DELUXE)"),
        ("Album", None, "Album"),
    ],
)
def test_versions(title, version, expected):
    assert with_version(title, version) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Jane, Composer, Lyricist", {"composer": ["Jane"], "lyricist": ["Jane"]}),
        ("Jane, Producer, UnrecognizedRole", {"producer": ["Jane"]}),
        ("Jane, Main Artist, Engineer", {"engineer": ["Jane"]}),
        ("Jane, MusicPublisher", {}),
        (
            "Jane, MainArtist - Guest, FeaturedArtist",
            {"main": ["Jane"], "featured": ["Guest"]},
        ),
        ("Jane, Guitar", {"performer": ["Jane (Guitar)"]}),
    ],
)
def test_credits(raw, expected):
    assert contributor_credits(raw) == expected


def test_anchored_comma_name():
    assert contributor_credits(
        "Earth, Wind & Fire, MainArtist", ["Earth, Wind & Fire"]
    ) == {"main": ["Earth, Wind & Fire"]}


def test_gain_validation():
    assert replaygain_tags(
        {
            "replaygain_track_gain": 0,
            "replaygain_track_peak": 0,
            "replaygain_album_gain": float("nan"),
            "replaygain_album_peak": True,
        }
    ) == {"replaygain_track_gain": "0.00 dB", "replaygain_track_peak": "0"}


def test_flac_roundtrip(tmp_path):
    path = tmp_path / "test.flac"
    shutil.copy("tests/silence.flac", path)
    FLAC(path).delete()
    meta = metadata()
    asyncio.run(tag_file(str(path), meta, None))
    tags = FLAC(path)
    assert tags["producer"] == ["Trina Shoemaker"]
    assert tags["composer"] == ["John Darnielle"]
    assert tags["lyricist"] == ["John Darnielle"]
    assert tags["artists"] == ["The Mountain Goats"]
    assert tags["albumartists"] == ["The Mountain Goats"]
    assert tags["barcode"] == ["0673855084121"]
    assert tags["qobuz_album_id"] == ["s72ps0jshvpwa"]
    assert tags["qobuz_track_id"] == ["216020864"]
    assert tags["replaygain_track_gain"] == ["-7.08 dB"]
    assert tags["replaygain_track_peak"] == ["0.936676"]
    assert tags["itunesadvisory"] == ["0"]
    assert tags["copyright"] == [BASE["copyright"]]


def test_exclude_roundtrip(tmp_path):
    path = tmp_path / "test.flac"
    shutil.copy("tests/silence.flac", path)
    FLAC(path).delete()
    asyncio.run(
        tag_file(
            str(path),
            metadata(),
            None,
            exclude=[
                "PRODUCER",
                "ReplayGain_Track_Gain",
                "artists",
                "COMPOSER",
                "description",
            ],
        )
    )
    tags = FLAC(path)
    assert not {
        "producer",
        "replaygain_track_gain",
        "artists",
        "composer",
        "description",
    } & set(tags.keys())
    assert tags["lyricist"] == ["John Darnielle"]


@pytest.mark.parametrize("warning, expected", [(True, "1"), (False, "0"), (None, None)])
def test_advisory(warning, expected):
    resp = copy.deepcopy(BASE)
    resp["parental_warning"] = warning
    resp.pop("isrc")
    meta = metadata(resp)
    assert meta.itunesadvisory == expected
    assert meta.info.explicit == (warning is True)
    assert meta.isrc is None


@pytest.mark.parametrize(
    "raw",
    [
        "Jane, UnrecognizedRole, Producer",
        "Jane, Smith, Producer",
    ],
)
def test_ambiguous_name_boundary_is_not_guessed(raw):
    assert contributor_credits(raw, ["Jane"]) == {}


def test_shorter_anchor_does_not_truncate_name():
    assert contributor_credits("Jane Smith, Producer", ["Jane"]) == {
        "producer": ["Jane Smith"]
    }


def test_structured_featured_precedence():
    assert structured_artists(
        [
            {"name": "Lead", "roles": ["main-artist"]},
            {"name": "Guest", "roles": ["main-artist", "featured-artist"]},
        ]
    ) == (["Lead"], ["Guest"])


def test_track_featured_precedence_across_sources():
    resp = copy.deepcopy(BASE)
    resp["performer"] = {"name": "Lead"}
    resp["artists"] = [
        {"name": "Lead", "roles": ["main-artist"]},
        {"name": "Guest", "roles": ["main-artist"]},
    ]
    resp["performers"] = "Lead, MainArtist - Guest, Main Artist, FeaturedArtist"
    assert metadata(resp).artist == "Lead feat. Guest"


@pytest.mark.parametrize(
    "entries",
    [
        [
            {"name": "Guest", "roles": ["main-artist"]},
            {"name": "Guest", "roles": ["featured-artist"]},
        ],
        [
            {"name": "Beyonce", "roles": ["main-artist"]},
            {"name": "Beyoncé", "roles": ["featured-artist"]},
        ],
    ],
)
def test_featured_precedence_across_structured_entries(entries):
    main, featured = structured_artists(entries)
    assert main == []
    assert featured == [entries[-1]["name"]]


def test_all_explicit_featured_credits_are_retained():
    resp = copy.deepcopy(BASE)
    resp["performer"] = {"name": "Lead"}
    resp["artists"] = [
        {"name": "Lead", "roles": ["main-artist"]},
        {"name": "First Guest", "roles": ["featured-artist"]},
    ]
    resp["performers"] = "Lead, MainArtist - Second Guest, FeaturedArtist"
    meta = metadata(resp)
    assert meta.artist == "Lead feat. First Guest, Second Guest"
    assert meta.artists == ["Lead", "First Guest", "Second Guest"]


def test_album_featured_precedence():
    resp = copy.deepcopy(BASE["album"])
    resp["artists"] = [
        {"name": "Lead", "roles": ["main-artist"]},
        {"name": "Guest", "roles": ["main-artist", "featured-artist"]},
    ]
    album = AlbumMetadata.from_qobuz(resp)
    assert album.albumartist == "Lead feat. Guest"
    assert album.albumartists == ["Lead", "Guest"]


def test_known_name_preserves_supported_credit_with_unknown_role():
    assert contributor_credits(
        "Smith, Jane, Producer, UnrecognizedRole", ["Smith, Jane"]
    ) == {"producer": ["Smith, Jane"]}


def test_album_primary_spelling_does_not_duplicate_featured_artist():
    resp = copy.deepcopy(BASE["album"])
    resp["artist"] = {"name": "Beyonce"}
    resp["artists"] = [{"name": "Beyoncé", "roles": ["featured-artist"]}]
    album = AlbumMetadata.from_qobuz(resp)
    assert album.albumartist == "Beyoncé"
    assert album.albumartists == ["Beyoncé"]
