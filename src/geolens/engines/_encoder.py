"""Frozen-encoder cosine-similarity baseline shared by all three engines.

No trained checkpoint ships with this workbench, so the frozen encoder is what
runs on a CPU tier. One query is served in four steps: the encoder and its
tokenizer load on the first call and are cached for the process; city
embeddings are computed once per (encoder, city-set, description function)
tuple and written under <cache root>/city_embeddings/ (see `geolens.paths`);
the query text is encoded with mean pooling over the last hidden state; cosine
similarity against the cached embeddings is softmaxed and cut to top-k.

The Prediction carries `note="real:<engine>"`, so a caller can tell a real
answer from a placeholder.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from geolens.paths import CITY_EMBEDDINGS, cache_subdir

logger = logging.getLogger(__name__)


@dataclass
class _LoadedModel:
    tokenizer: Any
    model: Any
    device: str


_MODEL_CACHE: dict[str, _LoadedModel] = {}
_CITY_EMB_CACHE: dict[str, Any] = {}  # city-set key -> torch.Tensor


def _load_encoder(model_name: str) -> _LoadedModel:
    if model_name in _MODEL_CACHE:
        return _MODEL_CACHE[model_name]

    import torch
    from transformers import AutoModel, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Loading encoder %s on %s (first call; ~30-60s on CPU)", model_name, device)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name).to(device).eval()
    bundle = _LoadedModel(tokenizer=tokenizer, model=model, device=device)
    _MODEL_CACHE[model_name] = bundle
    return bundle


def _mean_pool(last_hidden: Any, attention_mask: Any) -> Any:
    mask = attention_mask.unsqueeze(-1).float()
    return (last_hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)


def _embed_texts(model_name: str, texts: list[str], max_length: int = 256) -> Any:
    """Return an L2-normalized embedding tensor for `texts`, shape (N, hidden)."""
    import torch

    bundle = _load_encoder(model_name)
    inputs = bundle.tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    ).to(bundle.device)
    with torch.no_grad():
        out = bundle.model(**inputs)
    pooled = _mean_pool(out.last_hidden_state, inputs["attention_mask"])
    return torch.nn.functional.normalize(pooled, p=2, dim=1)


def _cache_key(model_name: str, cities: list[str], description_fn_id: str) -> str:
    h = hashlib.sha256(
        f"{model_name}::{description_fn_id}::{'|'.join(cities)}".encode()
    ).hexdigest()[:16]
    return h


def _city_embeddings(
    model_name: str,
    cities: list[str],
    describe: Callable[[str], str],
    description_fn_id: str,
) -> Any:
    """Return cached city embeddings (encoder-specific, set-specific)."""
    import torch

    key = _cache_key(model_name, cities, description_fn_id)
    if key in _CITY_EMB_CACHE:
        return _CITY_EMB_CACHE[key]

    disk_path = cache_subdir(CITY_EMBEDDINGS, create=True) / f"{key}.pt"
    if disk_path.exists():
        embs = torch.load(disk_path, weights_only=True)
        _CITY_EMB_CACHE[key] = embs
        return embs

    descriptions = [describe(c) for c in cities]
    logger.info("Computing %d city embeddings for %s (one-time)", len(cities), model_name)
    embs = _embed_texts(model_name, descriptions)
    torch.save(embs, disk_path)
    _CITY_EMB_CACHE[key] = embs
    return embs


def encoder_similarity_predict(
    *,
    engine_name: str,
    encoder: str,
    query_text: str,
    cities: list[str],
    describe_city: Callable[[str], str],
    description_fn_id: str,
    k: int = 5,
) -> Any:
    """Run the frozen-encoder cosine-similarity baseline and return a Prediction."""
    import torch

    from geolens.engines.base import Prediction

    start = time.perf_counter()
    city_embs = _city_embeddings(encoder, cities, describe_city, description_fn_id)
    query_emb = _embed_texts(encoder, [query_text])
    sims = (query_emb @ city_embs.T).squeeze(0)  # cosine, since both normalized
    probs = torch.softmax(sims * 10, dim=0)  # temperature 0.1 sharpens softly
    top = torch.topk(probs, k=min(k, len(cities)))
    top_k = [
        (cities[i.item()], float(p.item()))
        for p, i in zip(top.values, top.indices, strict=True)
    ]
    latency_ms = (time.perf_counter() - start) * 1000

    return Prediction(
        city=top_k[0][0],
        confidence=top_k[0][1],
        top_k=top_k,
        latency_ms=latency_ms,
        cost_usd=0.0,
        note=f"real:{engine_name} (encoder-only baseline)",
    )
