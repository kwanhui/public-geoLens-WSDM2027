"""Bounds on what one request may carry.

Every field here reaches four paid LLM calls on a hosted instance. Each cap is
sized for what the field carries: one social media post, a timeline of recent
posts from one account, and a bulk run an instance can afford.
"""

from __future__ import annotations

from typing import Literal

MIN_K = 1
MAX_K = 20

MAX_POST_CHARS = 2_000
MAX_TIMELINE_POSTS = 50
MAX_TIMELINE_CHARS = 20_000
MAX_HANDLE_CHARS = 100
MAX_ROW_ID_CHARS = 200

# The hard ceiling on `inputs` in a JSON batch body. MAX_BATCH_ROWS, which a
# deployment sets, is the operational cap and is usually lower; this one is
# declared on the model so the schema states a bound rather than accepting a
# list of any length and refusing it after parsing.
MAX_BATCH_INPUTS = 10_000

# The text one bulk run may carry, over every row. A JSON body is parsed
# before a handler sees it, so this is what bounds the work a request can ask
# for when the client declares no content length.
MAX_BATCH_CHARS = 200_000

FusionMethodName = Literal["weighted", "rrf"]
FUSION_METHODS = ("weighted", "rrf")


class InputError(ValueError):
    """A request field outside its bounds, with a message to show the caller."""


def clean_post(text: str | None, *, field: str = "post") -> str | None:
    """Trim a post; None when it is empty or only whitespace."""
    if text is None:
        return None
    trimmed = text.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_POST_CHARS:
        raise InputError(
            f"{field} is {len(trimmed)} characters; the limit is {MAX_POST_CHARS}, "
            "which is well above the length of a social media post."
        )
    return trimmed


def clean_timeline(posts: list[str] | None) -> list[str] | None:
    """Trim a user timeline, drop blank lines, and bound it. None when empty."""
    if posts is None:
        return None
    trimmed = [p.strip() for p in posts if p and p.strip()]
    if not trimmed:
        return None
    if len(trimmed) > MAX_TIMELINE_POSTS:
        raise InputError(
            f"user_posts has {len(trimmed)} entries; the limit is {MAX_TIMELINE_POSTS}."
        )
    for post in trimmed:
        if len(post) > MAX_POST_CHARS:
            raise InputError(
                f"A post in user_posts is {len(post)} characters; the limit is "
                f"{MAX_POST_CHARS}."
            )
    total = sum(len(p) for p in trimmed)
    if total > MAX_TIMELINE_CHARS:
        raise InputError(
            f"user_posts is {total} characters in total; the limit is "
            f"{MAX_TIMELINE_CHARS}."
        )
    return trimmed


def clean_handle(handle: str | None) -> str | None:
    if handle is None:
        return None
    trimmed = handle.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_HANDLE_CHARS:
        raise InputError(
            f"user_handle is {len(trimmed)} characters; the limit is {MAX_HANDLE_CHARS}."
        )
    return trimmed


def check_fusion_method(value: str) -> str:
    if value not in FUSION_METHODS:
        raise InputError(
            f"ensemble_method must be one of {', '.join(FUSION_METHODS)}; got {value!r}."
        )
    return value


def check_k(value: int) -> int:
    if not (MIN_K <= value <= MAX_K):
        raise InputError(f"k must be between {MIN_K} and {MAX_K}; got {value}.")
    return value
