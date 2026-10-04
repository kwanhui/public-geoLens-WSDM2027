"""Normalising a place name, and the identifier the catalogue hands out.

Every name is normalised to NFC before it is compared, stored or matched, and
`name_key` is the normalised, case-folded form two spellings of one place
share. `place_name_key` folds further, over NFKC and the homoglyphs one script
borrows from another, so a Cyrillic "о" inside "Tokyo" resolves to the entry
the catalogue already holds. C0 and C1 controls are stripped and the remaining
characters are restricted to letters, marks, digits and four punctuation marks.

`place_id` derives an opaque identifier from the normalised key. It is the same
for a given place on every instance and across restarts.
"""

from __future__ import annotations

import hashlib
import unicodedata

# What a place name may be written with, on top of letters, marks and digits.
# The comma is not here: it separates the catalogue names inside a prompt.
NAME_PUNCTUATION = frozenset(" -'.")

# The characters that open and close markup. A profile field carrying either
# is refused rather than escaped, because the field is read back by the
# gazetteer, by RetrieveZero's passage builder and by the page.
MARKUP_CHARACTERS = frozenset("<>")

PLACE_ID_PREFIX = "pl_"
PLACE_ID_HEX_CHARS = 12

# Letters one script borrows from the look of another. Folding them is what
# makes a name spelled with a Greek omicron resolve to the Latin entry rather
# than entering the catalogue beside it.
CONFUSABLE_FOLD: dict[str, str] = {
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ѕ": "s", "і": "i", "ј": "j", "к": "k",
    "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
    "х": "x", "ѐ": "e", "ё": "e",
    # Greek
    "α": "a", "β": "b", "γ": "y", "ε": "e", "ζ": "z", "η": "n", "ι": "i",
    "κ": "k", "μ": "u", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "ω": "w",
    # Armenian and Cherokee letters that read as Latin
    "օ": "o", "ո": "n", "ս": "u", "Ꭰ": "d", "Ꭱ": "r", "Ꭺ": "a",
    # Fullwidth Latin is handled by NFKC; these are the remaining strays.
    "ı": "i", "ȷ": "j", "ɑ": "a", "ɡ": "g", "ɩ": "i", "ĸ": "k", "ſ": "s",
    "ŉ": "n", "ƅ": "b", "ƚ": "l", "ɓ": "b", "ɗ": "d", "ɖ": "d", "ɸ": "o",
}

# Scripts that are written together inside one word. Anything else mixing
# inside a word is a homoglyph attempt rather than a spelling.
SCRIPT_GROUPS: dict[str, str] = {
    "HIRAGANA": "JAPANESE",
    "KATAKANA": "JAPANESE",
    "CJK": "JAPANESE",
}

# Character classes that belong to no script and never decide a word's.
SCRIPT_NEUTRAL = frozenset({"COMBINING", "DIGIT", "MODIFIER", "VARIATION", "ZERO"})


def strip_controls(text: str) -> str:
    """Drop C0 and C1 control characters, including NUL and the ANSI escape.

    `unicodedata.category` reports both ranges as Cc. Tab, newline and
    carriage return are controls too and are turned into spaces rather than
    dropped, so two words do not run together.
    """
    out = []
    for ch in text:
        if ch in "\t\n\r":
            out.append(" ")
        elif unicodedata.category(ch) == "Cc":
            continue
        else:
            out.append(ch)
    return "".join(out)


def normalise_text(text: str) -> str:
    """NFC, controls removed, whitespace collapsed, ends trimmed."""
    if not text:
        return ""
    return " ".join(strip_controls(unicodedata.normalize("NFC", text)).split())


def normalise_place_name(text: str) -> str:
    """`normalise_text` over NFKC, so a compatibility spelling folds first.

    NFKC resolves the fullwidth, circled and ligature forms of a letter to the
    plain one, which is what stops two spellings of one place from entering
    the catalogue side by side.
    """
    if not text:
        return ""
    return normalise_text(unicodedata.normalize("NFKC", text))


def name_key(name: str) -> str:
    """The form two spellings of one place share: normalised and case-folded."""
    return normalise_text(name).casefold()


def place_name_key(name: str) -> str:
    """`name_key` over NFKC with the homoglyphs folded to their Latin letter.

    Two names with this key in common are the same place however they were
    typed, so one of them is refused rather than added beside the other.
    """
    folded = normalise_place_name(name).casefold()
    return "".join(CONFUSABLE_FOLD.get(ch, ch) for ch in folded)


def place_id(name: str) -> str:
    """Stable opaque identifier for a place, derived from its normalised key.

    Two instances of GeoLens give the same place the same identifier, and a
    place keeps it across a restart, which a running counter would not.
    """
    digest = hashlib.sha256(name_key(name).encode("utf-8")).hexdigest()
    return PLACE_ID_PREFIX + digest[:PLACE_ID_HEX_CHARS]


def disallowed_name_characters(name: str) -> list[str]:
    """The characters in `name` that a place name may not contain.

    Letters, marks and digits are allowed in any script, plus the space,
    hyphen, apostrophe and full stop that place names are written with.
    Everything else, the comma and markup and slashes and brackets included,
    is refused.
    """
    bad: list[str] = []
    for ch in name:
        if ch in NAME_PUNCTUATION:
            continue
        if unicodedata.category(ch)[0] in "LMN":
            continue
        if ch not in bad:
            bad.append(ch)
    return bad


def script_of(ch: str) -> str | None:
    """The script a letter belongs to, or None when it belongs to none.

    Read off the first word of the character's Unicode name, which names the
    script for every letter this validation cares about. Japanese is one
    script here, because its three are written inside one word.
    """
    if unicodedata.category(ch)[0] != "L":
        return None
    try:
        first = unicodedata.name(ch).split()[0]
    except ValueError:
        return None
    if first in SCRIPT_NEUTRAL:
        return None
    return SCRIPT_GROUPS.get(first, first)


def mixed_script_words(name: str) -> list[str]:
    """The words of `name` whose letters come from more than one script."""
    out: list[str] = []
    for word in name.split():
        scripts = {s for s in (script_of(ch) for ch in word) if s}
        if len(scripts) > 1 and word not in out:
            out.append(word)
    return out


def markup_characters(text: str) -> list[str]:
    """The markup characters in `text`, in the order they first appear."""
    seen: list[str] = []
    for ch in text:
        if ch in MARKUP_CHARACTERS and ch not in seen:
            seen.append(ch)
    return seen
