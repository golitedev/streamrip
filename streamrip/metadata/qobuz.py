"""Interpret Qobuz credits without splitting display names into invented artists."""

import logging
import re
from datetime import date

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
}
# These establish credit boundaries but must not become performing artists.
KNOWN_ROLES = (
    set(ROLE_TAGS)
    | PERFORMER_ROLES
    | {
        "musicpublisher",
        "publisher",
        "executiveproducer",
        "vocalproducer",
        "masteringengineer",
        "recordingsecondengineer",
        "assistantengineer",
        "assistantrecordingengineer",
        "assistantmixingengineer",
        "studiopersonnel",
        "programmer",
        "programming",
        "production",
        "other",
        "artist",
        "masterer",
        "beats",
        "musicproduction",
        "programmingengineer",
        "digitaleditor",
        "sampledartist",
        "drumprogrammer",
        "graphicdesign",
        "adapter",
        "videowriter",
        "unknown",
        "keyboardsprogrammer",
        "voiceeditor",
        "additionalproduction",
        "synthesizerprogrammer",
    }
)


def text(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def unique_names(values) -> list[str]:
    names, seen = [], set()
    for value in values:
        name = text(value)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def role_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


def artist_credit(main: list[str], featured: list[str]) -> str:
    main = unique_names(main)
    featured = [
        n
        for n in unique_names(featured)
        if n.casefold() not in {v.casefold() for v in main}
    ]
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
        if not keys or "mainartist" in keys:
            main.append(entry["name"])
        elif "featuredartist" in keys:
            featured.append(entry["name"])
    return unique_names(main), unique_names(featured)


def contributor_credits(raw, known_names=()) -> dict[str, list[str]]:
    """Use explicit role suffixes; ambiguous names/unknown suffixes are omitted."""
    credits: dict[str, list[str]] = {}
    if not isinstance(raw, str):
        return credits
    anchors = sorted(unique_names(known_names), key=len, reverse=True)
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
        anchored = next(
            (
                name
                for name in anchors
                if segment[: len(name) + 2].casefold() == (name + ", ").casefold()
            ),
            None,
        )
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
            roles = [p.strip() for p in segment[len(name) + 2 :].split(",")]
        else:
            parts = [p.strip() for p in segment.split(",")]
            boundary = next(
                (i for i in range(1, len(parts)) if role_key(parts[i]) in KNOWN_ROLES),
                None,
            )
            if boundary != 1 or any(role_key(p) not in KNOWN_ROLES for p in parts[1:]):
                logger.debug("Unparsed Qobuz contributor credit: %s", segment)
                continue
            name, roles = parts[0], parts[1:]
            name = next(
                (known for known in anchors if name.casefold() == known.casefold()),
                name,
            )
        if not name:
            continue
        for role in roles:
            key = role_key(role)
            for tag in ROLE_TAGS.get(key, ()):
                credits.setdefault(tag, []).append(name)
            if key in PERFORMER_ROLES:
                value = (
                    name
                    if key in {"performer", "associatedperformer"}
                    else f"{name} ({role})"
                )
                credits.setdefault("performer", []).append(value)
    return {tag: unique_names(names) for tag, names in credits.items()}


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
