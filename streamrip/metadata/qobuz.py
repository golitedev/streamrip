"""Interpret Qobuz credits without splitting display names into invented artists."""

import logging
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

logger = logging.getLogger("streamrip")

ROLE_TAGS = {
    "mainartist": ("main",),
    "featuredartist": ("featured",),
    "composer": ("composer",),
    "composerlyricist": ("composer", "lyricist"),
    "lyricist": ("lyricist",),
    "writer": ("writer",),
    "producer": ("producer",),
    "coproducer": ("producer",),
    "arranger": ("arranger",),
    "conductor": ("conductor",),
    "remixer": ("remixer",),
    "mixer": ("mixer",),
    "mixingengineer": ("mixer",),
    "engineer": ("engineer",),
    "recordingengineer": ("engineer",),
    "liverecordingengineer": ("engineer",),
    "audiorestoration": ("engineer",),
    "additionalengineer": ("engineer",),
    "recordedby": ("engineer",),
    "additionalproducer": ("producer",),
    "producedby": ("producer",),
    "studioproducer": ("producer",),
    "workarranger": ("arranger",),
    "performancearranger": ("arranger",),
    "recordingarranger": ("arranger",),
    "vocalarranger": ("arranger",),
    "songwriter": ("writer",),
    "author": ("writer",),
    "remix": ("remixer",),
    "director": ("director",),
    "coarranger": ("arranger",),
    "musicdirector": ("director",),
    "musicdirectorconductor": ("director", "conductor"),
    "remixingengineer": ("engineer",),
    "stringarranger": ("arranger",),
    "stringsarranger": ("arranger",),
    "hornarranger": ("arranger",),
    "hornsarranger": ("arranger",),
    "guitararranger": ("arranger",),
    "orchestrator": ("arranger",),
    "additionalstudioproducer": ("producer",),
    "associateproducer": ("producer",),
    "assistantproducer": ("producer",),
    "recordingproducer": ("producer",),
    "vocalproducer": ("producer",),
    "additionalproduction": ("producer",),
    "codirector": ("director",),
    "audiorecordingengineer": ("engineer",),
    "masteringengineer": ("engineer",),
    "masterer": ("engineer",),
    "remasteringengineer": ("engineer",),
    "assistantengineer": ("engineer",),
    "assistantrecordingengineer": ("engineer",),
    "asstrecordingengineer": ("engineer",),
    "assistantadditionalrecordingengineer": ("engineer",),
    "assistantmasteringengineer": ("engineer",),
    "recordingsecondengineer": ("engineer",),
    "masteringsecondengineer": ("engineer",),
    "secondengineer": ("engineer",),
    "ndengineer": ("engineer",),  # Qobuz's "2nd Engineer", after role_key.
    "chiefengineer": ("engineer",),
    "studioengineer": ("engineer",),
    "vocalengineer": ("engineer",),
    "vocalrecordingengineer": ("engineer",),
    "stringrecordingengineer": ("engineer",),
    "hornsengineer": ("engineer",),
    "assistantstringsengineer": ("engineer",),
    "overdubengineer": ("engineer",),
    "programmingengineer": ("engineer",),
    "digitaleditingengineer": ("engineer",),
    "vocaleditingengineer": ("engineer",),
    "mixengineer": ("mixer",),
    "comixingengineer": ("mixer",),
    "additionalmixingengineer": ("mixer",),
    "assistantmixingengineer": ("engineer",),
    "assistantmixengineer": ("engineer",),
    "assistantmixer": ("engineer",),
    "mixingsecondengineer": ("engineer",),
    "remixingsecondengineer": ("engineer",),
    "engineermixer": ("engineer", "mixer"),
}
PERFORMER_ROLES = {
    "performer",
    "associatedperformer",
    "vocals",
    "vocal",
    "vocalist",
    "leadvocals",
    "leadvocalist",
    "leadsinger",
    "singer",
    "additionalvocals",
    "backgroundvocals",
    "backgroundvocal",
    "backgroundvocalist",
    "backingvocals",
    "guitar",
    "acousticguitar",
    "electricguitar",
    "bass",
    "bassguitar",
    "doublebass",
    "drums",
    "percussion",
    "keyboards",
    "keyboard",
    "piano",
    "synthesizer",
    "saxophone",
    "trumpet",
    "trombone",
    "flute",
    "clarinet",
    "violin",
    "viola",
    "cello",
    "strings",
    "accordion",
    "accordionist",
    "harmonica",
    "harp",
    "organ",
    "orchestra",
    "ensemble",
    "choir",
    "soloist",
    "featuredvocals",
    "withvocals",
    "voice",
    "voices",
    "chorus",
    "tenorvocalist",
    "bassvocalist",
    "sopranovocalist",
    "rapvocalist",
    "additionalvocalist",
    "soprano",
    "tenor",
    "baritone",
    "mezzosoprano",
    "sopranovocals",
    "tenorvocals",
    "baritonevocals",
    "withperformance",
    "performance",
    "musician",
    "allinstruments",
    "rap",
    "banjo",
    "sitar",
    "harpsichord",
    "vocoder",
    "speaker",
    "dj",
    "turntables",
    "melodica",
    "clapping",
    "mezzosopranovocals",
    "secondvocals",
    "treble",
    "bassbaritonevocals",
    "boysoprano",
    "vocalaccompaniment",
    "spokenvoice",
    "voicesamples",
    "fiddle",
    "guitaranytype",
    "interprete",
    "studiomusician",
    "additionalmusicians",
    "instruments",
    "ukulele",
    "mandolin",
    "mandola",
    "dobro",
    "steelguitar",
    "pedalsteelguitar",
    "lapsteelguitar",
    "rhythmguitar",
    "leadguitar",
    "acousticbaritoneguitar",
    "requintoguitar",
    "acousticbass",
    "acousticcello",
    "violoncello",
    "oboe",
    "bassoon",
    "horn",
    "horns",
    "frenchhorn",
    "brass",
    "tuba",
    "flugelhorn",
    "cornet",
    "altosaxophone",
    "tenorsaxophone",
    "baritonesaxophone",
    "leadviolin",
    "additionalkeyboards",
    "additionalpiano",
    "hammondorgan",
    "cembalo",
    "bandoneon",
    "acordeon",
    "vocalensemble",
    "rapperformedby",
    "claps",
    "bongos",
    "congas",
    "guiro",
    "bells",
    "timbales",
    "tambourine",
    "triangle",
    "timpani",
    "marimba",
    "maracas",
    "shaker",
    "shekere",
    "snaredrum",
    "bassdrum",
    "programmer",
    "programming",
    "drumprogrammer",
    "drumprogramming",
    "keyboardsprogrammer",
    "keyboardsprogramming",
    "synthesizerprogrammer",
    "synthesizerprogramming",
    "rhythmprogrammer",
    "additionalprogrammer",
}
# Additional roles establish boundaries without implying a performing artist.
KNOWN_ROLES = (
    set(ROLE_TAGS)
    | PERFORMER_ROLES
    | {
        "musicpublisher",
        "publisher",
        "executiveproducer",
        "studiopersonnel",
        "production",
        "other",
        "artist",
        "beats",
        "musicproduction",
        "digitaleditor",
        "sampledartist",
        "graphicdesign",
        "adapter",
        "videowriter",
        "unknown",
        "voiceeditor",
        "artdirector",
        "directorharpsichord",
        "participant",
        "productionmanager",
        "chorusmaster",
        "postproducer",
        "assistant",
        "samples",
        "contributor",
    }
)


def text(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


class _DescriptionParser(HTMLParser):
    """Keep review text and paragraph breaks without embedding HTML in tags."""

    _BLOCKS = frozenset(
        {
            "article",
            "blockquote",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "hr",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tr",
            "ul",
        }
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_tag: str | None = None

    def handle_starttag(self, tag, attrs):
        if self.ignored_tag is not None:
            return
        if tag in {"head", "script", "style", "template"}:
            self.ignored_tag = tag
        elif tag in self._BLOCKS:
            self.parts.append("\n\n")
        elif tag in {"br", "li"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.ignored_tag is not None:
            if tag == self.ignored_tag:
                self.ignored_tag = None
            return
        if tag in self._BLOCKS:
            self.parts.append("\n\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.ignored_tag is None:
            self.parts.append(data)


def description_text(value) -> str:
    """Convert Qobuz's HTML descriptions to readable, entity-decoded text."""
    value = text(value)
    if value is None:
        return ""
    parser = _DescriptionParser()
    parser.feed(value)
    parser.close()
    lines = [
        re.sub(r"[^\S\n]+", " ", line).strip()
        for line in "".join(parser.parts).splitlines()
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def unique_names(values) -> list[str]:
    names, seen = [], set()
    for value in values:
        name = text(value)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def role_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", value).casefold())


def name_key(value: str) -> str:
    """Match spelling/case/diacritic variants against supplied structured names."""
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def artist_credit(main: list[str], featured: list[str]) -> str:
    featured = unique_names(featured)
    featured_keys = {name_key(n) for n in featured}
    main = [n for n in unique_names(main) if name_key(n) not in featured_keys]
    if main and featured:
        return ", ".join(main) + " feat. " + ", ".join(featured)
    return ", ".join(main or featured)


def structured_artists(entries) -> tuple[list[str], list[str]]:
    main, featured = [], []
    for entry in entries or []:
        if not isinstance(entry, dict) or not text(entry.get("name")):
            continue
        roles = entry.get("roles") or []
        if isinstance(roles, str):
            roles = [roles]
        keys = {role_key(r) for r in roles if isinstance(r, str)}
        if "featuredartist" in keys:
            featured.append(entry["name"])
        elif not keys or "mainartist" in keys:
            main.append(entry["name"])
    featured_keys = {name_key(n) for n in featured}
    return (
        unique_names(n for n in main if name_key(n) not in featured_keys),
        unique_names(featured),
    )


def contributor_credits(
    raw, known_names=(), *, artist_names=()
) -> dict[str, list[str]]:
    """Keep supported roles at a known name boundary; never guess artist names."""
    credits: dict[str, list[str]] = {}
    if not isinstance(raw, str):
        return credits
    anchors = sorted(unique_names(known_names), key=len, reverse=True)
    corroborated_artists = {name_key(n) for n in unique_names(artist_names)}
    # Protect structured names that themselves contain Qobuz's credit separator.
    protected = raw
    replacements = {}
    for index, name in enumerate(anchors):
        if " - " in name:
            marker = f"\x00{index}\x00"
            protected = re.sub(
                re.escape(name + ", "),
                lambda _: marker + ", ",
                protected,
                flags=re.IGNORECASE,
            )
            replacements[marker] = name
    dangling_name = False
    for segment in protected.split(" - "):
        for marker, name in replacements.items():
            segment = segment.replace(marker, name)
        segment = segment.strip()
        parts = [p.strip() for p in segment.split(",")]
        anchored = None
        boundary = None
        for candidate in anchors:
            size = candidate.count(",") + 1
            # An unknown token here could be another component of a name such
            # as "Smith, Jane", rather than a role. Do not truncate that name.
            if (
                len(parts) > size
                and name_key(", ".join(parts[:size])) == name_key(candidate)
                and role_key(parts[size]) in KNOWN_ROLES
            ):
                anchored, boundary = candidate, size
                break
        # An unanchored name containing " - " cannot safely be distinguished
        # from a credit separator. Do not turn its last word into an artist.
        if "," not in segment:
            dangling_name = bool(segment)
            logger.debug("Unparsed Qobuz contributor credit: %s", segment)
            continue
        if dangling_name and not anchored:
            dangling_name = False
            logger.debug("Unparsed Qobuz contributor credit: %s", segment)
            continue
        dangling_name = False
        if anchored:
            name = anchored
            roles = parts[boundary:]
        else:
            boundary = next(
                (i for i in range(1, len(parts)) if role_key(parts[i]) in KNOWN_ROLES),
                None,
            )
            if boundary != 1:
                logger.debug("Unparsed Qobuz contributor credit: %s", segment)
                continue
            name, roles = parts[0], parts[1:]
            name = next(
                (known for known in anchors if name_key(name) == name_key(known)),
                name,
            )
        if not name:
            continue
        unknown_roles = any(role_key(role) not in KNOWN_ROLES for role in roles)
        for role in roles:
            key = role_key(role)
            if key not in ROLE_TAGS and key not in PERFORMER_ROLES:
                logger.debug("Unmapped Qobuz contributor role: %s (%s)", role, name)
                continue
            if key == "mainartist" and role.strip().casefold() != "mainartist":
                # Legacy "Main Artist" credits can include engineers and writers.
                # Only structured artist fields can corroborate those entries;
                # a structured composer name establishes no artist credit.
                credits.setdefault("legacy_main", []).append(name)
                continue
            if (
                key in {"mainartist", "featuredartist"}
                and unknown_roles
                and name_key(name) not in corroborated_artists
            ):
                # Preserve engineering/writing credits without promoting an
                # ambiguous, partly understood free-text name to an artist.
                logger.debug("Ambiguous Qobuz artist credit: %s", segment)
                continue
            for tag in ROLE_TAGS.get(key, ()):
                credits.setdefault(tag, []).append(name)
            if key in PERFORMER_ROLES:
                value = (
                    name
                    if key in {"performer", "associatedperformer"}
                    else f"{name} ({role})"
                )
                credits.setdefault("performer", []).append(value)
    legacy = credits.pop("legacy_main", [])
    for name in legacy:
        if name_key(name) in corroborated_artists:
            credits.setdefault("main", []).append(name)
        else:
            logger.debug("Uncorroborated Qobuz legacy main artist: %s", name)
    # A specific featured credit takes precedence over a broad main-artist role.
    featured = {name_key(n) for n in credits.get("featured", [])}
    if "main" in credits:
        credits["main"] = [n for n in credits["main"] if name_key(n) not in featured]
    return {tag: unique_names(names) for tag, names in credits.items()}


def replaygain_tags(audio_info) -> dict[str, str]:
    """Copy supplied finite ReplayGain values; never calculate or invent gains."""
    tags = {}
    if not isinstance(audio_info, dict):
        return tags
    for scope in ("track", "album"):
        for kind in ("gain", "peak"):
            key = f"replaygain_{scope}_{kind}"
            value = audio_info.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (kind == "peak" and value < 0)
            ):
                continue
            tags[key] = (
                f"{value:.2f} dB"
                if kind == "gain"
                else format(Decimal(str(value)), "f")
            )
    return tags


def with_version(title: str, version) -> str:
    version = text(version)

    def key(value):
        return " ".join(re.findall(r"\w+", value.casefold()))

    if version and " " + key(version) + " " not in " " + key(title) + " ":
        return f"{title} ({version})"
    return title


def release_date(value) -> str | None:
    value = text(value)
    if value and re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        try:
            date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
            return value
        except ValueError:
            pass
    return None
