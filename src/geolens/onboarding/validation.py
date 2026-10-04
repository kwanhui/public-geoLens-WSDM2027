"""Bounds on what an operator may put into a place profile.

The catalogue on a hosted instance is shared, the gazetteer matches every alias
against every post, and a catalogue name is pasted verbatim into the
instruction part of the prompt both LLM classifiers send, so these rules stand
between one request and everybody else's results.

A place name is at most five words and sixty characters, carries no comma and
no sentence punctuation beyond a full stop inside an abbreviation, holds no
word that reads as an instruction, keeps one script per word, and does not
swallow a place the catalogue already holds. An alias or landmark is at least
3 characters and at most four words, is not made only of common English, Malay
or Indonesian words, and carries at least one token that is either capitalised
as typed or shares a stem with its place.

The rest is size bounds: at most 8 aliases and 8 landmarks, 12 foods and 12
slang entries, 80 characters per entry and 1,000 in the notes field. Every
field is normalised and stripped of control characters first; see
`geolens.places`.
"""

from __future__ import annotations

import math
import re

from geolens.places import (
    disallowed_name_characters,
    markup_characters,
    mixed_script_words,
    name_key,
    normalise_place_name,
    normalise_text,
    place_name_key,
)

MAX_NAME_LENGTH = 80  # the schema-level bound; a place name is held to 60
MAX_PLACE_NAME_CHARS = 60
MAX_PLACE_NAME_WORDS = 5
MAX_ABBREVIATION_LETTERS = 3
MIN_ALIAS_LENGTH = 3
MAX_ALIAS_WORDS = 4
MAX_ITEM_LENGTH = 80
MAX_LIST_ITEMS = 12
MAX_ALIASES = 8
MAX_LANDMARKS = 8
MAX_NOTES_LENGTH = 1000
MIN_STEM_LETTERS = 4

# How many words a name may add to a catalogue place it starts or ends with.
# "Kuala Lumpur City Centre" extends "Kuala Lumpur"; a sentence wrapped round
# a place name does not.
MAX_EXTENSION_WORDS = 2

# Words that would match a large share of ordinary English, Malay or
# Indonesian text. Not a stop-word list for retrieval: only the terms an alias
# or a landmark must never be built out of.
STOP_WORDS = frozenset(
    {
        # English function words
        "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
        "at", "back", "be", "been", "before", "but", "by", "can", "did", "do",
        "does", "down", "for", "from", "get", "go", "going", "had", "has",
        "have", "he", "her", "here", "him", "his", "how", "i", "if", "in",
        "into", "is", "it", "its", "just", "like", "me", "more", "most", "my",
        "new", "no", "not", "of", "off", "on", "one", "only", "or", "other",
        "our", "out", "over", "she", "so", "some", "still", "such", "than",
        "that", "the", "their", "them", "then", "there", "these", "they",
        "this", "those", "to", "too", "under", "up", "us", "very", "was",
        "we", "were", "what", "when", "where", "which", "who", "why", "will",
        "with", "would", "you", "your",
        # English time words
        "afternoon", "again", "always", "day", "days", "early", "evening",
        "every", "hour", "hours", "late", "later", "minute", "minutes",
        "month", "morning", "never", "night", "nights", "now", "often",
        "soon", "time", "today", "tomorrow", "tonight", "week", "weekend",
        "year", "yesterday",
        # Malay and Indonesian function and time words
        "ada", "adalah", "akan", "aku", "atau", "bagi", "banyak", "baru",
        "belum", "besok", "bisa", "buat", "dan", "dari", "dengan", "di",
        "dia", "dulu", "hari", "ingin", "ini", "itu", "jadi", "juga", "kalau",
        "kami", "kamu", "kan", "ke", "kemarin", "kita", "lagi", "lah", "lain",
        "lalu", "malam", "mau", "masih", "mereka", "nanti", "pada", "pagi",
        "pun", "saja", "sama", "saya", "sekarang", "semua", "siang", "sini",
        "situ", "sudah", "tadi", "tapi", "telah", "tidak", "untuk", "yang",
        # Interjections and chat shorthand
        "aduh", "haha", "hahaha", "hehe", "lmao", "lol", "oke", "omg", "wah",
        "wkwk", "wkwkwk", "yay",
    }
)

# Words that read as an instruction to the model the catalogue is listed for.
# A place name holding one of them is refused whatever else it says.
INSTRUCTION_WORDS = frozenset(
    {
        "above", "always", "answer", "below", "every", "ignore", "instruction",
        "instructions", "list", "never", "output", "post", "posts", "prompt",
        "reply", "respond", "return", "system",
    }
)

# Markers of a street address or a single dwelling.
ADDRESS_WORDS = frozenset(
    {
        "apartment", "apartments", "blk", "block", "condo", "flat", "home",
        "house", "lot", "residence", "residences", "room", "storey", "suite",
        "unit", "units",
    }
)

LIST_FIELDS = ("aliases", "landmarks", "foods", "slang")

LIST_CAPS = {"aliases": MAX_ALIASES, "landmarks": MAX_LANDMARKS}

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


class ProfileError(ValueError):
    """An edit the shared catalogue will not accept, with a reason to show."""


def _words(text: str) -> list[str]:
    """The alphabetic words of `text`, case preserved."""
    return _WORD.findall(text)


def _full_stops_are_abbreviations(name: str) -> bool:
    """Whether every full stop in `name` ends an abbreviation.

    A full stop is allowed when the letters directly before it are an
    abbreviation of at most three letters that starts a word, as in
    "St. Louis" and "D.C.". A full stop after a whole word ends a sentence,
    which a place name does not contain.
    """
    for i, ch in enumerate(name):
        if ch != ".":
            continue
        j = i
        while j > 0 and name[j - 1].isalpha():
            j -= 1
        letters = i - j
        if not 1 <= letters <= MAX_ABBREVIATION_LETTERS:
            return False
        if j > 0 and name[j - 1] not in " .":
            return False
    return True


def validate_place_name(raw: str) -> str:
    """Return the normalised place name, or raise with what is wrong with it."""
    name = normalise_place_name(raw or "")
    if not name:
        raise ProfileError("The place name is empty. Type the name of a place.")
    if len(name) > MAX_PLACE_NAME_CHARS:
        raise ProfileError(
            f"The place name is {len(name)} characters; the limit is "
            f"{MAX_PLACE_NAME_CHARS}."
        )
    words = name.split()
    if len(words) > MAX_PLACE_NAME_WORDS:
        raise ProfileError(
            f"The place name is {len(words)} words; the limit is "
            f"{MAX_PLACE_NAME_WORDS}. A place name is not a sentence."
        )
    if not any(ch.isalpha() for ch in name):
        raise ProfileError("The place name has no letters in it.")
    bad = disallowed_name_characters(name)
    if bad:
        raise ProfileError(
            "The place name contains "
            + ", ".join(repr(ch) for ch in bad[:5])
            + ". A place name may hold letters, marks, digits, spaces, hyphens, "
            "apostrophes and full stops, and nothing else: the name is listed "
            "verbatim in the prompt the LLM classifiers send."
        )
    if not _full_stops_are_abbreviations(name):
        raise ProfileError(
            "The place name holds a full stop that does not end an "
            "abbreviation. A full stop is allowed inside a name such as "
            "'St. Louis'; a place name is not a sentence."
        )
    found = sorted({w.lower() for w in _words(name)} & INSTRUCTION_WORDS)
    if found:
        raise ProfileError(
            "The place name contains "
            + ", ".join(repr(w) for w in found)
            + ". The catalogue is listed inside the prompt the LLM classifiers "
            "send, so a name that reads as an instruction is refused."
        )
    mixed = mixed_script_words(name)
    if mixed:
        raise ProfileError(
            "The word " + ", ".join(repr(w) for w in mixed[:3]) + " mixes letters "
            "from more than one script. Write each word in one script."
        )
    return name


def swallowed_catalogue_place(name: str, catalogue: list[str]) -> str | None:
    """A catalogue place `name` contains as a whole word without extending it.

    A name may extend a place already in the catalogue by up to two words at
    either end, which is what "Kuala Lumpur City Centre" does. A name that
    wraps a catalogue place in other text is refused: the name is listed in
    the prompt beside the place it names.
    """
    target = name_key(name)
    words = [w.lower() for w in name.split()]
    for entry in catalogue:
        entry_words = [w.lower() for w in entry.split()]
        if not entry_words or name_key(entry) == target:
            continue
        n = len(entry_words)
        if n > len(words):
            continue
        starts = words[:n] == entry_words
        ends = words[-n:] == entry_words
        inside = any(words[i : i + n] == entry_words for i in range(len(words) - n + 1))
        if not inside:
            continue
        if (starts or ends) and len(words) - n <= MAX_EXTENSION_WORDS:
            continue
        return entry
    return None


def confusable_catalogue_place(name: str, catalogue: list[str]) -> str | None:
    """The catalogue entry `name` folds onto, or None.

    Two names that share a `place_name_key` are the same place typed two ways,
    which the catalogue holds once.
    """
    target = place_name_key(name)
    if not target:
        return None
    return next((c for c in catalogue if place_name_key(c) == target), None)


def address_like_reason(name: str) -> str | None:
    """Why `name` reads as a building or a street address, or None."""
    if any(ch.isdigit() for ch in name):
        return (
            f"{name!r} contains a digit, which a building or a street address "
            "has and a place does not."
        )
    found = sorted({w.lower() for w in _words(name)} & ADDRESS_WORDS)
    if found:
        return (
            f"{name!r} contains " + ", ".join(repr(w) for w in found) + ", which "
            "names a dwelling rather than a place."
        )
    return None


def validate_field_text(field: str, raw: str) -> str:
    """Normalise one free-text profile value and refuse markup in it."""
    value = normalise_text(raw or "")
    found = markup_characters(value)
    if found:
        raise ProfileError(
            f"{field} contains " + ", ".join(repr(ch) for ch in found) + ". A profile "
            "field is read back by the gazetteer, by RetrieveZero and by the page, "
            "so it may not carry markup."
        )
    return value


def _shares_a_stem(token: str, place_name: str) -> bool:
    """Whether `token` and a word of `place_name` share a long enough prefix."""
    low = token.lower()
    for word in _words(place_name):
        other = word.lower()
        shared = 0
        for a, b in zip(low, other, strict=False):
            if a != b:
                break
            shared += 1
        if shared >= MIN_STEM_LETTERS:
            return True
    return False


def validate_name_like(field: str, raw: str, place_name: str = "") -> str:
    """Return a normalised alias or landmark, or raise if it matches ordinary text.

    The gazetteer counts these against every post, so each one has to look
    like a name: at most four words, not built only out of common words, and
    carrying at least one token that is capitalised as typed or shares a stem
    of four letters with the place it belongs to.
    """
    value = validate_field_text(field, raw)
    if not value:
        raise ProfileError(f"{field} is empty. Remove it or type a name.")
    if len(value) < MIN_ALIAS_LENGTH:
        raise ProfileError(
            f"{value!r} is shorter than {MIN_ALIAS_LENGTH} characters, so it would "
            "match text that has nothing to do with the place."
        )
    if len(value) > MAX_ITEM_LENGTH:
        raise ProfileError(f"{value!r} is longer than {MAX_ITEM_LENGTH} characters.")
    tokens = _words(value)
    if not tokens:
        raise ProfileError(f"{value!r} has no letters in it.")
    if len(value.split()) > MAX_ALIAS_WORDS:
        # The offending text is often several fields below the message, so
        # the message names the field it came from.
        raise ProfileError(
            f"{field}, {value!r}, is {len(value.split())} words; the limit is "
            f"{MAX_ALIAS_WORDS}."
        )
    if all(t.lower() in STOP_WORDS for t in tokens):
        raise ProfileError(
            f"{value!r} is made only of common words, so it would match almost any "
            "post. Use a name the place is actually known by."
        )
    if not any(
        t[:1].isupper() or _shares_a_stem(t, place_name) for t in tokens
    ):
        raise ProfileError(
            f"{value!r} does not read as a name: no word in it is capitalised, and "
            f"none shares the first {MIN_STEM_LETTERS} letters of "
            f"{place_name or 'the place name'!r}."
        )
    return value


def validate_alias(raw: str, place_name: str = "") -> str:
    """Return the normalised alias, or raise if it would match ordinary text."""
    return validate_name_like("An alias", raw, place_name)


def validate_list(field: str, values: list[str], place_name: str = "") -> list[str]:
    """Normalise, drop blanks and bound one of the profile's list fields."""
    items = [
        validate_field_text(f"An entry in {field}", str(v))
        for v in values
        if normalise_text(str(v))
    ]
    cap = LIST_CAPS.get(field, MAX_LIST_ITEMS)
    if len(items) > cap:
        raise ProfileError(f"{field} has {len(items)} entries; the limit is {cap}.")
    for item in items:
        if len(item) > MAX_ITEM_LENGTH:
            raise ProfileError(
                f"An entry in {field} is {len(item)} characters; the limit is "
                f"{MAX_ITEM_LENGTH}."
            )
    if field == "aliases":
        return [validate_name_like("An alias", a, place_name) for a in items]
    if field == "landmarks":
        return [validate_name_like("A landmark", m, place_name) for m in items]
    return items


def validate_notes(raw: str) -> str:
    notes = validate_field_text("The notes field", raw)
    if len(notes) > MAX_NOTES_LENGTH:
        raise ProfileError(
            f"The notes field is {len(notes)} characters; the limit is {MAX_NOTES_LENGTH}."
        )
    return notes


def validate_region(raw: str) -> str:
    """Normalise the country or region hint and refuse markup in it."""
    return validate_field_text("The region hint", raw)


def validate_coordinate(lat: float | None, lon: float | None) -> None:
    """Range-check a centroid. Either both are set or neither is.

    NaN and the infinities fail every comparison, so they are refused by name
    rather than slipping through the range check.
    """
    for field, value in (("Latitude", lat), ("Longitude", lon)):
        if value is not None and not math.isfinite(value):
            raise ProfileError(f"{field} {value} is not a finite number.")
    if lat is not None and not (-90.0 <= lat <= 90.0):
        raise ProfileError(f"Latitude {lat} is outside the range -90 to 90.")
    if lon is not None and not (-180.0 <= lon <= 180.0):
        raise ProfileError(f"Longitude {lon} is outside the range -180 to 180.")
