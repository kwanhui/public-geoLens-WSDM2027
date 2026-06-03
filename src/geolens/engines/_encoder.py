"""Frozen-encoder cosine-similarity baseline shared by all three engines.

This is the natural zero-shot starting point each engine paper improves upon
(via contrastive fine-tuning, prompt engineering, LLM-retrieved knowledge).
No trained checkpoints are available, so this is what the "real" mode runs,
and it fits on a HF Space CPU tier. It is a proper baseline, not a stub.

How it works for one query:
1. Lazy-load the encoder + tokenizer on first call (cached for the process).
2. Pre-compute city embeddings once per (encoder, city-set, description-fn)
   tuple, cache to disk under ~/.geolens/city_embeddings/.
3. Encode the query text with mean-pooling over the encoder's last hidden state.
4. Cosine similarity vs. cached city embeddings → softmax → top-k.

The Prediction returned has `note="real:<engine>"` so downstream code (and
anyone reading the API responses) can tell stub from real.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("GEOLENS_CACHE_DIR", str(Path.home() / ".geolens"))) / "city_embeddings"


@dataclass
class _LoadedModel:
    tokenizer: object
    model: object
    device: str


_MODEL_CACHE: dict[str, _LoadedModel] = {}
_CITY_EMB_CACHE: dict[str, "torch.Tensor"] = {}  # type: ignore[name-defined]  # noqa: F821


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


def _mean_pool(last_hidden: "torch.Tensor", attention_mask: "torch.Tensor"):  # type: ignore[name-defined]  # noqa: F821
    mask = attention_mask.unsqueeze(-1).float()
    return (last_hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)


def _embed_texts(model_name: str, texts: list[str], max_length: int = 256):  # noqa: F821
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
):
    """Return cached city embeddings (encoder-specific, set-specific)."""
    import torch

    key = _cache_key(model_name, cities, description_fn_id)
    if key in _CITY_EMB_CACHE:
        return _CITY_EMB_CACHE[key]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = CACHE_DIR / f"{key}.pt"
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
):
    """Run the frozen-encoder cosine-similarity baseline and return a Prediction."""
    import torch

    from geolens.engines.base import Prediction

    start = time.perf_counter()
    city_embs = _city_embeddings(encoder, cities, describe_city, description_fn_id)
    query_emb = _embed_texts(encoder, [query_text])
    sims = (query_emb @ city_embs.T).squeeze(0)  # cosine, since both normalized
    probs = torch.softmax(sims * 10, dim=0)  # temperature 0.1 sharpens softly
    top = torch.topk(probs, k=min(k, len(cities)))
    top_k = [(cities[i.item()], float(p.item())) for p, i in zip(top.values, top.indices)]
    latency_ms = (time.perf_counter() - start) * 1000

    return Prediction(
        city=top_k[0][0],
        confidence=top_k[0][1],
        top_k=top_k,
        latency_ms=latency_ms,
        cost_usd=0.0,
        note=f"real:{engine_name} (encoder-only baseline)",
    )
