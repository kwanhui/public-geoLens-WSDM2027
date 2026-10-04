"""GeoLens: a workbench for comparing zero-shot social media geolocation engines."""

from geolens.engines.base import Engine, Prediction
from geolens.triangulator.consensus import TriangulationResult, triangulate

# The one place the build version is written. pyproject.toml reads it with
# setuptools' dynamic `attr:`, and the FastAPI app and the run manifest
# both report it, so OpenAPI and the manifest cannot drift apart again.
__version__ = "0.14.5"

# The version of the HTTP contract, which moves only when the contract does.
# It is deliberately not the build version: a change to the page, a
# docstring or a script produces a new build and the same API. A new field on
# a response or a new optional parameter is a minor step; removing a field,
# renaming one or changing what a status code means is a major one. See
# CHANGELOG.md for what has moved.
API_VERSION = "1.0"

__all__ = ["API_VERSION", "Engine", "Prediction", "TriangulationResult", "triangulate"]
