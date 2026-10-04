"""The one roster of engines, and what a client is told about each.

`build_engines` is run by the server, the CLI and the WNUT adapter alike, so an
engine registered here reaches all three. `engine_metadata` is what
`GET /instance` serves: the label, the short tag and its tooltip, the
granularity, the family, and whether the engine sends text to a third party.
An engine the registry does not describe still comes back, described from its
own attributes.

`docs/adding-an-engine.md` is the walkthrough.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines.base import Engine
from geolens.engines.contrastgeo import ContrastGeoEngine
from geolens.engines.fewuser import FewUserEngine
from geolens.engines.gazetteer import GazetteerEngine
from geolens.engines.llm_classifier import LLMClassifierEngine
from geolens.engines.llm_claude import ClaudeClassifierEngine
from geolens.engines.retrievezero import RetrieveZeroEngine

FROZEN_ENCODER_NOTE = (
    "Hosted instantiation of the published method with a frozen off-the-shelf "
    "encoder. No support examples are used, and there is no way to supply any."
)


@dataclass(frozen=True)
class EngineSpec:
    """One entry of the roster: how to build the engine, and how to label it."""

    key: str
    label: str
    family: str
    granularity: str
    tag: str
    tag_title: str
    factory: Callable[[list[str]], Engine]


ENGINE_SPECS: tuple[EngineSpec, ...] = (
    EngineSpec(
        key="contrastgeo",
        label="ContrastGeo",
        family="contrastgeo",
        granularity="post",
        tag="frozen encoder",
        tag_title=FROZEN_ENCODER_NOTE + " It embeds the bare place name (SimCSE BERT-large).",
        factory=lambda cities: ContrastGeoEngine(cities=cities),
    ),
    EngineSpec(
        key="fewuser",
        label="FewUser",
        family="fewuser",
        granularity="user",
        tag="frozen encoder",
        tag_title=(
            FROZEN_ENCODER_NOTE
            + " It embeds the template 'a social media user from <name>' "
            "(SimCSE RoBERTa-large)."
        ),
        factory=lambda cities: FewUserEngine(cities=cities),
    ),
    EngineSpec(
        key="retrievezero",
        label="RetrieveZero",
        family="retrievezero",
        granularity="user",
        tag="frozen encoder",
        tag_title=(
            FROZEN_ENCODER_NOTE
            + " It embeds a passage built from the place's drafted profile "
            "(E5-large) when there is one, which there is only for an onboarded "
            "place; for every built-in place it embeds the bare name. It is the "
            "one engine an onboarded profile reaches."
        ),
        factory=lambda cities: RetrieveZeroEngine(cities=cities),
    ),
    EngineSpec(
        key="gazetteer_post",
        label="Gazetteer (post)",
        family="gazetteer",
        granularity="post",
        tag="string match",
        tag_title="Counts occurrences of each place name and its aliases in the text.",
        factory=lambda cities: GazetteerEngine(granularity="post", cities=cities),
    ),
    EngineSpec(
        key="gazetteer_user",
        label="Gazetteer (user)",
        family="gazetteer",
        granularity="user",
        tag="string match",
        tag_title="Counts occurrences of each place name and its aliases in the timeline.",
        factory=lambda cities: GazetteerEngine(granularity="user", cities=cities),
    ),
    EngineSpec(
        key="gpt4o_mini_post",
        label="GPT-4o-mini (post)",
        family="gpt",
        granularity="post",
        tag="prompted LLM",
        tag_title="Picks one place from the catalogue, which is listed in the prompt.",
        factory=lambda cities: LLMClassifierEngine(granularity="post", cities=cities),
    ),
    EngineSpec(
        key="gpt4o_mini_user",
        label="GPT-4o-mini (user)",
        family="gpt",
        granularity="user",
        tag="prompted LLM",
        tag_title="Picks one place from the catalogue, which is listed in the prompt.",
        factory=lambda cities: LLMClassifierEngine(granularity="user", cities=cities),
    ),
    EngineSpec(
        key="claude_haiku_post",
        label="Claude Haiku (post)",
        family="claude",
        granularity="post",
        tag="prompted LLM",
        tag_title="Picks one place from the catalogue, which is listed in the prompt.",
        factory=lambda cities: ClaudeClassifierEngine(granularity="post", cities=cities),
    ),
    EngineSpec(
        key="claude_haiku_user",
        label="Claude Haiku (user)",
        family="claude",
        granularity="user",
        tag="prompted LLM",
        tag_title="Picks one place from the catalogue, which is listed in the prompt.",
        factory=lambda cities: ClaudeClassifierEngine(granularity="user", cities=cities),
    ),
)

SPECS_BY_KEY: dict[str, EngineSpec] = {spec.key: spec for spec in ENGINE_SPECS}


def build_engines(catalogue: list[str] | None = None) -> tuple[dict[str, Engine], list[str]]:
    """The full workbench roster, plus the shared live catalogue.

    Cheap engines first (gazetteer, encoder) so the interface can show partial
    results before the expensive LLM calls land.

    Every engine is handed the *same* mutable catalogue list. Appending a newly
    onboarded place to that list makes it visible to all engines on the next
    request (the gazetteer matches its aliases, the LLM classifiers add it to
    the prompt, and the encoders re-embed it because their cache key is keyed
    on the place set), with no rebuild or restart.
    """
    places = list(DEFAULT_CITIES) if catalogue is None else catalogue
    engines = {spec.key: spec.factory(places) for spec in ENGINE_SPECS}
    return engines, places


def engine_metadata(engines: Mapping[str, Engine]) -> dict[str, dict[str, Any]]:
    """What a client needs to describe each engine on screen.

    An engine the roster does not hold is described from its own attributes,
    so adding one in a test or a fork does not need an edit here.
    """
    out: dict[str, dict[str, Any]] = {}
    for key, engine in engines.items():
        spec = SPECS_BY_KEY.get(key)
        out[key] = {
            "label": spec.label if spec else key,
            "family": spec.family if spec else getattr(engine, "name", key),
            "granularity": engine.granularity,
            "tag": spec.tag if spec else getattr(engine, "tag", ""),
            "tag_title": spec.tag_title if spec else getattr(engine, "tag_title", ""),
            "calls_a_third_party": bool(getattr(engine, "calls_a_third_party", False)),
        }
    return out


def local_engine_names(engines: Mapping[str, Engine]) -> list[str]:
    """The engines that send no text to a third party, in roster order."""
    return [
        name
        for name, engine in engines.items()
        if not getattr(engine, "calls_a_third_party", False)
    ]
