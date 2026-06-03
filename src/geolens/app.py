"""Entry point for `python -m geolens.app`.

Starts the FastAPI server on the requested host/port. HF Spaces (Docker SDK)
sets $PORT to 7860; we fall back to 7860 for local runs to match.
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from geolens.ui.server import create_app

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog="geolens.app")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "7860")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("GEOLENS_STUB_MODE", "0") == "1":
        logger.info("GEOLENS_STUB_MODE=1: engines return deterministic placeholder predictions.")

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
