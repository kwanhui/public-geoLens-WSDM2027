"""A placeholder-mode GeoLens instance for the browser tests to drive.

Run as a script, this serves the app on the port given as the first argument.
With ``GEOLENS_TEST_TOY_ENGINE=1`` it registers the toy engine from
``tests/test_engine_registry.py`` first, the way ``docs/adding-an-engine.md``
says an adopter would, so a browser test can check that an engine the server
registers reaches the page with no edit to the page.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def build() -> object:
    if os.environ.get("GEOLENS_TEST_TOY_ENGINE") == "1":
        sys.path.insert(0, str(TESTS))
        from test_engine_registry import TOY_SPEC  # noqa: PLC0415

        from geolens.engines import registry  # noqa: PLC0415

        registry.ENGINE_SPECS = (*registry.ENGINE_SPECS, TOY_SPEC)
        registry.SPECS_BY_KEY[TOY_SPEC.key] = TOY_SPEC
    from geolens.ui.server import create_app  # noqa: PLC0415

    return create_app()


def main() -> None:
    import uvicorn

    port = int(sys.argv[1])
    uvicorn.run(build(), host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    main()
