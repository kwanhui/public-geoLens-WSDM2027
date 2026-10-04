"""The vocabulary of the interface's visible text, and its outward links.

The static bundle has no JavaScript test harness, so these read the files:
the rendered text of `index.html` and the string literals of `app.js`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/geolens/ui/static"
INDEX = STATIC / "index.html"
APP_JS = STATIC / "app.js"

# "account-level": the paper and the rest of the interface say user-level.
BANNED_IN_VISIBLE_TEXT = ["blend", "triangulat", "degraded", "verdict", "account-level"]


def js_string_literals(source: str) -> list[str]:
    """Every string literal in `source`, with comments and code skipped.

    JSON field names such as `triangulation` are part of the API contract and
    stay; what the reader sees must not use them. Scanning the literals rather
    than the whole file is what separates the two.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            i = source.find("\n", i)
            if i < 0:
                break
        elif ch == "/" and i + 1 < n and source[i + 1] == "*":
            i = source.find("*/", i)
            i = n if i < 0 else i + 2
        elif ch in "\"'`":
            quote, j, buf = ch, i + 1, []
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == quote:
                    break
                if quote != "`" and source[j] == "\n":
                    break  # an unterminated literal is not one
                buf.append(source[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
        else:
            i += 1
    return out


def visible_prose() -> list[tuple[str, str]]:
    out = [("index.html", re.sub(r"<[^>]+>", " ", INDEX.read_text()))]
    out += [("app.js", lit) for lit in js_string_literals(APP_JS.read_text())]
    return out


@pytest.mark.parametrize("word", BANNED_IN_VISIBLE_TEXT)
def test_the_interface_uses_the_papers_vocabulary(word: str) -> None:
    """One name per object, and the one the paper uses."""
    for name, text in visible_prose():
        assert word.lower() not in text.lower(), f"{name} still says {word!r} in {text!r}"


PUBLIC_REPO = "https://github.com/kwanhui/public-geoLens-WSDM2027"


def test_every_visitor_facing_link_resolves_for_a_visitor() -> None:
    """A GitHub link a visitor can follow names the public repository."""
    paths = [INDEX, ROOT / "README.md", ROOT / "eval/README.md", ROOT / "pyproject.toml",
             ROOT / "CITATION.cff", *sorted((ROOT / "demo").glob("*.md"))]
    for path in paths:
        for repo in re.findall(r"github\.com/kwanhui/[\w.-]+", path.read_text()):
            assert repo.removesuffix(".git") == PUBLIC_REPO.removeprefix("https://"), (path, repo)


def test_the_header_and_the_format_spec_point_at_the_public_repository() -> None:
    html = INDEX.read_text()
    assert f'href="{PUBLIC_REPO}"' in html
    assert f'href="{PUBLIC_REPO}/blob/main/eval/README.md"' in html
