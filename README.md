![streamrip logo](https://github.com/nathom/streamrip/blob/dev/demo/logo.svg?raw=true)

[![Downloads](https://pepy.tech/badge/streamrip)](https://pepy.tech/project/streamrip)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/python/black)

A scriptable stream downloader for Qobuz, Tidal, Deezer and SoundCloud.

![downloading an album](https://github.com/nathom/streamrip/blob/dev/demo/download_album.png?raw=true)

## Features

- Fast, concurrent downloads powered by `aiohttp`
- Downloads tracks, albums, playlists, discographies, and labels from Qobuz, Tidal, Deezer, and SoundCloud
- Supports downloads of Spotify and Apple Music playlists through [last.fm](https://www.last.fm)
- Automatically converts files to a preferred format
- Has a database that stores the downloaded tracks' IDs so that repeats are avoided
- Concurrency and rate limiting
- Interactive search for all sources
- Highly customizable through the config file
- Integration with `youtube-dl`

### Qobuz metadata in this checkout

Qobuz FLAC downloads retain the album's edition/version in `ALBUM` and the folder
name, without appending a version that is already present. Track versions and
classical work title prefixes are retained as well.

The FLAC writer also embeds these fields when Qobuz supplies them:

- `LABEL` and `BARCODE` (from `upc`, retaining leading zeros).
- `ARTISTS` and `ALBUMARTISTS` as separate Vorbis values, while `ARTIST` and
  `ALBUMARTIST` contain display credits. Explicit main/featured roles determine
  the display credit, including `feat.` where applicable. Producers, composers
  and other contributors are not promoted to performing artists. Qobuz's broad
  legacy `Main Artist` entries require corroboration from structured artist
  fields, because those entries can include studio staff. Explicit
  featured credits take precedence over a broad main-artist role, including
  across structured and free-text credits. Explicit featured names from both
  sources are retained.
- Individual values for `COMPOSER`, `LYRICIST`, `WRITER`, `PRODUCER`, `ARRANGER`,
  `CONDUCTOR`, `REMIXER`, `MIXER`, `ENGINEER`, `DIRECTOR` and `PERFORMER`, where
  contributor roles can be interpreted reliably. Instruments/vocal roles are included in
  `PERFORMER` values. Names are not split on commas; ambiguous free-text credits
  are omitted rather than guessed and can be inspected with debug logging.
  Supplied structured names establish boundaries and preserve capitalization and
  diacritics; matching a shorter name prefix is not enough to truncate a name.
  String/horn arrangers and orchestrators use `ARRANGER`; mastering and assistant
  engineering credits use `ENGINEER`. Studio musicians, instruments and programming
  use `PERFORMER`. These credits do not add people to `ARTISTS` or `ALBUMARTISTS`.
  At a recognized name/role boundary, an unsupported role no longer discards the
  same person's supported credits. Partly understood free-text artist credits
  still require corroboration from structured artist names.
  Publishers, administrative roles and ambiguous comma-containing names are not
  forced into unrelated tags; unresolved credits/roles are logged at debug level.
  In particular, a music publisher is not substituted for the record `LABEL`.
- `GENRE` from Qobuz's primary `genre.name`, preserving the upstream fix for
  French genres. The `genres_list` hierarchy can be French and is not used,
  including as a fallback when the primary name is absent. A name such as
  `Hip-Hop/Rap` is retained as one genre. The writer still supports multiple values.
- `ORIGINALDATE` from `release_date_original`. `DATE` uses Qobuz's
  `release_date_download`, then `release_date_stream`, then `release_date`, with
  the original date as a fallback. Dates reflect the supplied catalog data;
  a remaster year is not inferred from an edition title. `YEAR` follows `DATE`.
- `WORK`, separately from the existing work prefix in `TITLE`.
- `COPYRIGHT` uses the supplied track copyright, falling back to the album
  copyright when the track has none. Copyright symbols are normalized as before.
- `REPLAYGAIN_TRACK_GAIN` and `REPLAYGAIN_TRACK_PEAK` from Qobuz's `audio_info`,
  with gain expressed in dB and peak as a linear value. Only supplied finite
  numeric values are copied, including valid zero values. Album gain/peak tags
  are copied only if supplied; no album values are calculated or guessed, and
  the audio is not normalized or re-encoded.
- `ITUNESADVISORY=1` for an explicit track and `0` when Qobuz supplies a false
  parental warning. A missing flag is omitted; false does not establish an edited
  clean version (`2`). The track's explicit flag also drives filename formatting.
- `QOBUZ_ALBUM_ID` and `QOBUZ_TRACK_ID` as custom source identifiers. The album ID
  is the public `id` used in the Qobuz album URL, not the separate numeric
  `qobuz_id`. The `{id}` album folder placeholder uses this public ID too.

Existing ISRC support is retained, and a missing ISRC no longer prevents tagging
a Qobuz track. The additional tag mappings above apply to FLAC; they do not add
these new fields to MP3 or AAC output. No external metadata provider is queried.
The existing `metadata.exclude` setting is now honored during tagging, including
the added artist, contributor and ReplayGain fields. Exclusion names are case
insensitive (for example, `description` or `REPLAYGAIN_TRACK_GAIN`). Exclusion
prevents writing a field; it does not delete pre-existing tags from an input file.

Field names follow the [MusicBrainz Picard Vorbis mappings](https://picard-docs.musicbrainz.org/en/latest/appendices/tag_mapping.html),
apart from the explicitly named Qobuz custom IDs and the iTunes advisory convention.

## Qobuz lyrics

Qobuz lyrics are optional and disabled by default. Configure them under `[qobuz]`
in Streamrip's `config.toml`:

```toml
[qobuz]
download_lyrics = true
lyrics_mode = "sidecar"   # "sidecar", "embedded", or "both"
lyrics_synced = true      # prefer timestamps; false requests plain-text output
lyrics_overwrite = false  # preserve existing sidecar files
```

This applies to Qobuz albums, singles, and playlists. Streamrip requests the
original-language lyrics directly from Qobuz's signed `track/lyricsUrl` endpoint,
then reads the lyrics document. It does not use an external lyrics provider.
Availability depends on the track. Unavailable lyrics, network errors, and sidecar
write errors are logged or skipped without failing the audio download.

- **Sidecar:** saves UTF-8 lyrics beside the final audio file, with the same stem.
  Timestamped lyrics use `.lrc`; plain text uses `.txt`. For example,
  `01 - Song.flac` gets `01 - Song.lrc`. Word-synced documents are reduced to
  line timestamps. Untimed verse separators remain in plain text; lines without
  timestamps cannot be represented in LRC.
- **Embedded:** writes the selected plain or LRC text to the normal lyrics tag
  (`LYRICS` for FLAC, `USLT` for MP3, or the MP4 lyrics field). This does not create
  a binary ID3 `SYLT` frame.
- **Both:** embeds lyrics and saves the matching sidecar.

With `lyrics_synced = true`, tracks without usable timestamps fall back to plain
text. Setting `lyrics_synced = false` produces plain text even when Qobuz supplies
timings. `metadata.exclude = ["lyrics"]` suppresses both fetching and output.
Existing sidecars are preserved unless `lyrics_overwrite = true`; disabling lyrics
does not delete existing tags or files. These settings do not change the existing
lyrics behavior of other providers, and do not backfill already downloaded tracks.

When used inside CineRoute, these options can be passed through its
`music.streamrip.qobuz` YAML configuration. CineRoute must be rebuilt with a pin to
this Streamrip commit before that deployment gains the feature.

## Qobuz FLAC integrity

Qobuz FLAC downloads are decoded and verified before tagging, conversion, or
being marked downloaded. Missing STREAMINFO MD5 checksums are calculated from
the decoded PCM and written into the existing 16-byte field. Audio frames are
not re-encoded. Nonzero checksums are verified and never overwritten. Decoding
errors, length mismatches, and durations more than three seconds away from the
supplied catalog duration fail the track. A failed attempt is retried once;
failed files are removed and are not recorded as downloaded.

Install the `flac` command-line tool separately (for example, `apt install flac`
on Ubuntu). In the `[qobuz]` config section, `verify_flac = true` enables this
step and `flac_validation_timeout = 1800` limits each validation to 30 minutes.
Existing configs use these defaults without migration. Set `verify_flac = false`
to disable validation and checksum repair. MP3 downloads and other services are
unaffected by this Qobuz-specific validation setting.

Validation is cancellable and reports decoder errors. The checksum records the
audio received; it is not a checksum obtained from Qobuz's original master.

## Installation

First, ensure [Python](https://www.python.org/downloads/) (version 3.10 or greater) and [pip](https://pip.pypa.io/en/stable/installing/) are installed. Then install `ffmpeg`. You may choose not to install this, but some functionality will be limited.

```bash
pip3 install streamrip --upgrade
```

If you run into issues, try installing directly from the `dev` branch:

```bash
pip3 install git+https://github.com/nathom/streamrip.git@dev
```

When you type

```bash
rip
```

it should show the main help page. If you have no idea what these mean, or are having other issues installing, check out the [detailed installation instructions](https://github.com/nathom/streamrip/wiki#detailed-installation-instructions).

For Arch Linux users, an AUR package exists. Make sure to install required packages from the AUR before using `makepkg` or use an AUR helper to automatically resolve them.
```
git clone https://aur.archlinux.org/streamrip.git
cd streamrip
makepkg -si
```
 or
 ```
paru -S streamrip
```

Alternatively, for users of Homebrew, you can install streamrip through brew.
```
brew install streamrip
```

### Streamrip beta

If you want to get access to the latest and greatest features without waiting for a new release, install
from the `dev` branch with the following command

```bash
pip3 install git+https://github.com/nathom/streamrip.git@dev
```

## Example Usage

**For Tidal and Qobuz, you NEED a premium subscription.**

Download an album from Qobuz

```bash
rip url https://www.qobuz.com/us-en/album/rumours-fleetwood-mac/0603497941032
```

Download multiple albums from Qobuz

```bash
rip url https://www.qobuz.com/us-en/album/back-in-black-ac-dc/0886444889841 https://www.qobuz.com/us-en/album/blue-train-john-coltrane/0060253764852
```

Download the album and convert it to `mp3`

```bash
rip --codec mp3 url https://open.qobuz.com/album/0060253780968
```

To set the maximum quality, use the `--quality` option to `0, 1, 2, 3, 4`:

| Quality ID | Audio Quality         | Available Sources                            |
| ---------- | --------------------- | -------------------------------------------- |
| 0          | 128 kbps MP3 or AAC   | Deezer, Tidal, SoundCloud (most of the time) |
| 1          | 320 kbps MP3 or AAC   | Deezer, Tidal, Qobuz, SoundCloud (rarely)    |
| 2          | 16 bit, 44.1 kHz (CD) | Deezer, Tidal, Qobuz, SoundCloud (rarely)    |
| 3          | 24 bit, ≤ 96 kHz      | Tidal (MQA), Qobuz, SoundCloud (rarely)      |
| 4          | 24 bit, ≤ 192 kHz     | Qobuz                                        |

```bash
rip --quality 3 url https://tidal.com/browse/album/147569387
```

> Using `4` is generally a waste of space. It is impossible for humans to perceive the difference between sampling rates higher than 44.1 kHz. It may be useful if you're processing/slowing down the audio.

Search for playlists matching `rap` on Tidal

```bash
rip search tidal playlist 'rap'
```

![streamrip interactive search](https://github.com/nathom/streamrip/blob/dev/demo/playlist_search.png?raw=true)

Search for *Rumours* on Tidal, and download it

```bash
rip search tidal album 'fleetwood mac rumours'
```

Download a last.fm playlist using the lastfm command

```
rip lastfm https://www.last.fm/user/nathan3895/playlists/12126195
```

For more customization, see the config file

```
rip config open
```

If you're confused about anything, see the help pages. The main help pages can be accessed by typing `rip` by itself in the command line. The help pages for each command can be accessed with the `--help` flag. For example, to see the help page for the `url` command, type

```
rip url --help
```

![example_help_page.png](https://github.com/nathom/streamrip/blob/dev/demo/example_help_page.png?raw=true)

## Other information

For more in-depth information about `streamrip`, see the help pages and the [wiki](https://github.com/nathom/streamrip/wiki/).

## Contributions

All contributions are appreciated! You can help out the project by opening an issue
or by submitting code.

### Issues

If you're opening an issue **use the Feature Request or Bug Report templates properly**. This ensures
that I have all of the information necessary to debug the issue. If you do not follow the templates,
**I will silently close the issue** and you'll have to deal with it yourself.

### Code

If you're new to Git, follow these steps to open your first Pull Request (PR):

- Fork this repository
- Clone the new repository
- Commit your changes
- Open a pull request to the `dev` branch

Please document any functions or obscure lines of code.

### The Wiki

To help out `streamrip` users that may be having trouble, consider contributing some information to the wiki.
Nothing is too obvious and everything is appreciated.

## Acknowledgements

Thanks to Vitiko98, Sorrow446, and DashLt for their contributions to this project, and the previous projects that made this one possible.

`streamrip` was inspired by:

- [qobuz-dl](https://github.com/vitiko98/qobuz-dl)
- [Qo-DL Reborn](https://github.com/badumbass/Qo-DL-Reborn)
- [Tidal-Media-Downloader](https://github.com/yaronzz/Tidal-Media-Downloader)
- [scdl](https://github.com/flyingrub/scdl)

## Disclaimer

I will not be responsible for how **you** use `streamrip`. By using `streamrip`, you agree to the terms and conditions of the Qobuz, Tidal, and Deezer APIs.

## Sponsorship

Consider becoming a Github sponsor for me if you enjoy my open source software.
