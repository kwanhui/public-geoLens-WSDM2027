"""GeoLens: unified workbench for few-shot and zero-shot social media geolocation."""

from geolens.engines.base import Engine, Prediction
from geolens.triangulator.consensus import TriangulationResult, triangulate

__version__ = "0.8.0"

__all__ = ["Engine", "Prediction", "TriangulationResult", "triangulate"]
