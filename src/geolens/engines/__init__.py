"""Engine adapters wrapping three few-shot and zero-shot geolocation engines plus public baselines."""

from geolens.engines.base import Engine, GeolocateInput, Prediction
from geolens.engines.contrastgeo import ContrastGeoEngine
from geolens.engines.fewuser import FewUserEngine
from geolens.engines.gazetteer import GazetteerEngine
from geolens.engines.llm_classifier import LLMClassifierEngine
from geolens.engines.llm_claude import ClaudeClassifierEngine
from geolens.engines.retrievezero import RetrieveZeroEngine

__all__ = [
    "Engine",
    "GeolocateInput",
    "Prediction",
    "ContrastGeoEngine",
    "FewUserEngine",
    "RetrieveZeroEngine",
    "GazetteerEngine",
    "LLMClassifierEngine",
    "ClaudeClassifierEngine",
]
