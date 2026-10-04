"""Evaluation metrics: Acc@1, Acc@5, mean rank, distance error, latency, cost.

Computed only over rows with `status=ok` and a ground truth in the catalogue;
OOC and error rows are reported separately. The distance metrics are median
and mean great-circle error in km and Acc@161 km. See `geolens.geo`.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from geolens.batch.runner import BatchRow
from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._coords import coords_for
from geolens.geo import ACC_KM_THRESHOLD, haversine_km
from geolens.stats import wilson_interval


@dataclass
class CityCount:
    """How many rows a corpus places in a given city (the 'where is the
    conversation coming from' view), per granularity bucket.

    Each count is split by whether the text named a catalogue place at that
    level. The engines always answer, so a corpus of posts that name no place
    still fills this table, and on one 30-row run the language of the post
    decided the column it landed in. The split says which counts rest on a
    named place and which do not."""

    city: str
    post_count: int = 0
    user_count: int = 0
    post_named: int = 0
    post_unnamed: int = 0
    user_named: int = 0
    user_unnamed: int = 0


GAZETTEER_NAMES = {"post": "gazetteer_post", "user": "gazetteer_user"}


def named_a_place(row: BatchRow, level: str) -> bool | None:
    """Whether the gazetteer found a catalogue place in this row's text.

    None when the gazetteer did not run at that level, so a caller can tell
    "no place named" from "not asked".
    """
    pred = row.per_engine.get(GAZETTEER_NAMES[level])
    if pred is None or not pred.usable:
        return None
    return not pred.abstain


def gazetteer_abstentions(rows: list[BatchRow]) -> dict[str, int]:
    """Rows at each level whose text named no catalogue place."""
    return {
        level: sum(1 for r in rows if named_a_place(r, level) is False)
        for level in ("post", "user")
    }


def gazetteer_matches(rows: list[BatchRow]) -> dict[str, int]:
    """Rows at each level whose text did name a catalogue place."""
    return {
        level: sum(1 for r in rows if named_a_place(r, level) is True)
        for level in ("post", "user")
    }


def compute_rollup(rows: list[BatchRow]) -> list[CityCount]:
    """Aggregate predicted locations across a corpus into per-city counts.

    Unlike the accuracy summary this needs no ground truth, so it works on the
    plain batch-prediction path too. Sorted by total count, descending.
    """
    counts: dict[str, CityCount] = {}

    def _bump(city: str | None, bucket: str, named: bool | None) -> None:
        if not city:
            return
        cc = counts.setdefault(city, CityCount(city=city))
        if bucket == "post":
            cc.post_count += 1
            if named is True:
                cc.post_named += 1
            elif named is False:
                cc.post_unnamed += 1
        else:
            cc.user_count += 1
            if named is True:
                cc.user_named += 1
            elif named is False:
                cc.user_unnamed += 1

    for r in rows:
        if r.status != "ok":
            continue
        post = r.ensembles.get("post")
        user = r.ensembles.get("user")
        _bump(post.consensus_city if post else None, "post", named_a_place(r, "post"))
        _bump(user.consensus_city if user else None, "user", named_a_place(r, "user"))
    return sorted(
        counts.values(), key=lambda c: (c.post_count + c.user_count), reverse=True
    )


@dataclass
class EngineMetrics:
    name: str
    acc_at_1: float = 0.0
    acc_at_5: float = 0.0
    # 95% Wilson score intervals for Acc@1 / Acc@5, so a reader does not
    # over-read a gap between two engines that is within sampling noise.
    acc_at_1_ci: tuple[float, float] = (0.0, 0.0)
    acc_at_5_ci: tuple[float, float] = (0.0, 0.0)
    mean_rank: float = 0.0  # rank of ground truth in top-k; len(top_k)+1 if absent
    # Mean rank over the rows where the truth was in the top-k list, with how
    # many rows those were. `mean_rank` averages a 1,000,000 sentinel for a
    # row the engine abstained on, so `mean_rank` is not interpretable for an
    # engine that abstains often; read `mean_rank_found` and `n_rank_found`
    # instead.
    mean_rank_found: float = 0.0
    n_rank_found: int = 0
    # Distance error of the top-1 prediction vs. ground truth (geolocation
    # convention). Rows the engine abstained on are excluded, because it
    # named no place and is therefore neither near nor far, and scoring an
    # abstention as a miss would place a median of 0 km beside an engine that
    # named no place.
    median_error_km: float = 0.0
    mean_error_km: float = 0.0
    acc_at_161km: float = 0.0
    n_geo: int = 0  # rows contributing to the distance metrics
    n_abstained: int = 0  # rows where the engine named no place at all
    median_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    n_evaluated: int = 0


@dataclass
class EnsembleMetrics:
    granularity: str
    acc_at_1: float = 0.0
    acc_at_5: float = 0.0
    acc_at_1_ci: tuple[float, float] = (0.0, 0.0)
    acc_at_5_ci: tuple[float, float] = (0.0, 0.0)
    mean_rank: float = 0.0
    mean_rank_found: float = 0.0
    n_rank_found: int = 0
    median_error_km: float = 0.0
    acc_at_161km: float = 0.0
    n_geo: int = 0
    n_evaluated: int = 0
    # How often the ensemble's top-1 differed from the best single engine in the bucket.
    differs_from_best_single_rate: float = 0.0


@dataclass
class BucketMetrics:
    """Per-difficulty-bucket Acc@1 for each engine, so the aggregate table can
    be read alongside where each method actually wins or fails."""

    bucket: str
    n_rows: int = 0
    acc_at_1: dict[str, float] = field(default_factory=dict)  # engine name -> Acc@1


@dataclass
class BannerMetrics:
    """Precision/recall of the cross-task disagreement banner against gold
    `should_disagree` labels, when the uploaded set provides them. Lets the
    banner's reliability be reported as a number rather than asserted."""

    n_labelled: int = 0
    # Of the labelled rows, how many carry a user timeline. Only those can
    # ever fire: a post-only row does not run the user-level engines, so it
    # has no user-level consensus to compare the post against and enters the
    # table as a forced true negative.
    n_with_timeline: int = 0
    n_positive: int = 0  # rows that should fire the banner
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    precision: float = 0.0
    recall: float = 0.0


@dataclass
class EvalSummary:
    total_rows: int = 0
    evaluated_rows: int = 0
    ooc_rows: int = 0
    error_rows: int = 0
    # Closed-set size: how many candidate cities each engine chooses among.
    # Acc@1 is uninterpretable without it.
    catalogue_size: int = 0
    per_engine: dict[str, EngineMetrics] = field(default_factory=dict)
    ensembles: dict[str, EnsembleMetrics] = field(default_factory=dict)
    per_bucket: dict[str, BucketMetrics] = field(default_factory=dict)
    banner: BannerMetrics | None = None


def _rank_in_topk(top_k: list[tuple[str, float]], target: str) -> int:
    """1-indexed rank of `target` in `top_k`. Returns len(top_k)+1 if absent."""
    if not top_k:
        # An empty list would otherwise score as rank 1, which reads as a hit.
        return 1_000_000
    target_l = target.lower()
    for i, (city, _) in enumerate(top_k, start=1):
        if city.lower() == target_l:
            return i
    return len(top_k) + 1


def _distance_eval(pred_city: str, gt_city: str) -> tuple[float | None, bool]:
    """Return (error_km, within_161km) for a top-1 prediction vs. ground truth.

    error_km is None when either city has no coordinate; within_161km is False
    in that case (an answer we cannot place on the map is not within 100 miles).
    """
    gt = coords_for(gt_city)
    if gt is None:
        return None, False
    pred = coords_for(pred_city)
    if pred is None:
        return None, False
    err = haversine_km(pred, gt)
    return err, err <= ACC_KM_THRESHOLD


def _summarise_distance(errors: list[float], within: list[bool]) -> tuple[float, float, float]:
    """median_error_km, mean_error_km (over known-coord rows), acc_at_161km (over `within`)."""
    median_km = statistics.median(errors) if errors else 0.0
    mean_km = sum(errors) / len(errors) if errors else 0.0
    acc161 = sum(1 for w in within if w) / len(within) if within else 0.0
    return median_km, mean_km, acc161


def bucket_of(row: BatchRow) -> str:
    """Difficulty bucket for a row: explicit `bucket`, else the id prefix
    (``hard-sem-3`` -> ``hard-sem``), else ``other``."""
    if row.bucket:
        return row.bucket
    m = re.match(r"^(.*?)-?\d+$", row.id or "")
    return (m.group(1) if m and m.group(1) else (row.id or "other")) or "other"


def truth_for(row: BatchRow, granularity: str | None) -> str | None:
    """The ground truth a given level is scored against.

    A `disagree` row carries two truths: where the post was written and where
    the account lives. Scoring the user-level engines against the post's city
    is what stopped `/eval` from reproducing the paper's table.
    """
    if granularity == "user" and row.ground_truth_user_city:
        return row.ground_truth_user_city
    return row.ground_truth_city


def compute_summary(
    rows: list[BatchRow],
    catalogue_size: int | None = None,
    granularities: Mapping[str, str] | None = None,
) -> EvalSummary:
    """Metrics per engine and per fusion level.

    `granularities` maps engine name to "post" or "user". With it, and with a
    `ground_truth_user_city` on a row, the user-level engines and the
    user-level fusion are scored against the account's home city and the
    post-level ones against the post's city. Without either, every engine is
    scored against `ground_truth_city`, which is what the endpoint has always
    done.
    """
    granularities = granularities or {}
    summary = EvalSummary(
        total_rows=len(rows),
        catalogue_size=catalogue_size if catalogue_size is not None else len(DEFAULT_CITIES),
    )
    summary.ooc_rows = sum(1 for r in rows if r.status == "ooc")
    summary.error_rows = sum(1 for r in rows if r.status == "error")

    eligible = [
        r for r in rows
        if r.status == "ok" and (r.ground_truth_city or r.ground_truth_user_city)
    ]
    summary.evaluated_rows = len(eligible)
    if not eligible:
        return summary

    # Collect engine names from the first eligible row that has them.
    engine_names = list(eligible[0].per_engine.keys())

    for engine in engine_names:
        ranks: list[int] = []
        latencies: list[float] = []
        costs: list[float] = []
        errors: list[float] = []
        within: list[bool] = []
        found_ranks: list[int] = []
        hits1 = 0
        hits5 = 0
        n = 0
        n_abstained = 0
        for r in eligible:
            pred = r.per_engine.get(engine)
            if pred is None or not pred.usable:
                # An engine that was not run, or whose call failed, is not
                # scored on that row rather than scored as a miss.
                continue
            truth = truth_for(r, granularities.get(engine))
            if not truth:
                continue
            n += 1
            rank = _rank_in_topk(pred.top_k, truth)
            ranks.append(rank)
            if rank <= len(pred.top_k):
                found_ranks.append(rank)
            if rank == 1:
                hits1 += 1
            if rank <= 5:
                hits5 += 1
            latencies.append(pred.latency_ms)
            costs.append(pred.cost_usd)
            if pred.abstain or not pred.city:
                n_abstained += 1
                continue
            err_km, is_within = _distance_eval(pred.city, truth)
            if coords_for(truth) is not None:
                within.append(is_within)
                if err_km is not None:
                    errors.append(err_km)
        if n == 0:
            continue
        median_km, mean_km, acc161 = _summarise_distance(errors, within)
        summary.per_engine[engine] = EngineMetrics(
            name=engine,
            acc_at_1=hits1 / n,
            acc_at_5=hits5 / n,
            acc_at_1_ci=wilson_interval(hits1, n),
            acc_at_5_ci=wilson_interval(hits5, n),
            mean_rank=sum(ranks) / n,
            mean_rank_found=(sum(found_ranks) / len(found_ranks) if found_ranks else 0.0),
            n_rank_found=len(found_ranks),
            median_error_km=median_km,
            mean_error_km=mean_km,
            acc_at_161km=acc161,
            n_geo=len(within),
            n_abstained=n_abstained,
            median_latency_ms=statistics.median(latencies) if latencies else 0.0,
            total_cost_usd=sum(costs),
            n_evaluated=n,
        )

    for granularity in ("post", "user"):
        ens_ranks: list[int] = []
        ens_found_ranks: list[int] = []
        ens_errors: list[float] = []
        ens_within: list[bool] = []
        hits1 = 0
        hits5 = 0
        differs = 0
        n = 0
        for r in eligible:
            er = r.ensembles.get(granularity)
            truth = truth_for(r, granularity)
            if er is None or not truth:
                continue
            n += 1
            rank = _rank_in_topk(er.top_k, truth)
            ens_ranks.append(rank)
            if rank <= len(er.top_k):
                ens_found_ranks.append(rank)
            if rank == 1:
                hits1 += 1
            if rank <= 5:
                hits5 += 1
            if er.differs_from_best_single:
                differs += 1
            err_km, is_within = _distance_eval(er.consensus_city, truth)
            if coords_for(truth) is not None:
                ens_within.append(is_within)
                if err_km is not None:
                    ens_errors.append(err_km)
        if n == 0:
            continue
        median_km, _mean_km, acc161 = _summarise_distance(ens_errors, ens_within)
        summary.ensembles[granularity] = EnsembleMetrics(
            granularity=granularity,
            acc_at_1=hits1 / n,
            acc_at_5=hits5 / n,
            acc_at_1_ci=wilson_interval(hits1, n),
            acc_at_5_ci=wilson_interval(hits5, n),
            mean_rank=sum(ens_ranks) / n,
            mean_rank_found=(
                sum(ens_found_ranks) / len(ens_found_ranks) if ens_found_ranks else 0.0
            ),
            n_rank_found=len(ens_found_ranks),
            median_error_km=median_km,
            acc_at_161km=acc161,
            n_geo=len(ens_within),
            n_evaluated=n,
            differs_from_best_single_rate=differs / n,
        )

    # Per-difficulty-bucket Acc@1 per engine (stratified view).
    buckets: dict[str, list[BatchRow]] = defaultdict(list)
    for r in eligible:
        buckets[bucket_of(r)].append(r)
    for bname, brows in buckets.items():
        bm = BucketMetrics(bucket=bname, n_rows=len(brows))
        for engine in engine_names:
            hits = tot = 0
            for r in brows:
                pred = r.per_engine.get(engine)
                truth = truth_for(r, granularities.get(engine))
                if pred is None or not pred.usable or not truth:
                    continue
                tot += 1
                if _rank_in_topk(pred.top_k, truth) == 1:
                    hits += 1
            if tot:
                bm.acc_at_1[engine] = hits / tot
        summary.per_bucket[bname] = bm

    # Cross-task disagreement banner precision/recall, when the upload tags
    # which rows should fire it (the bundled set tags osint-* / disagree-*).
    labelled = [
        r for r in eligible
        if r.should_disagree is not None and r.triangulation is not None
    ]
    if labelled:
        bm2 = BannerMetrics(n_labelled=len(labelled))
        for r in labelled:
            assert r.triangulation is not None
            if r.triangulation.user_consensus_city:
                bm2.n_with_timeline += 1
        for r in labelled:
            assert r.triangulation is not None  # guaranteed by the filter above
            fired = bool(r.triangulation.disagreement_flag)
            gold = bool(r.should_disagree)
            bm2.n_positive += int(gold)
            if gold and fired:
                bm2.true_positive += 1
            elif fired and not gold:
                bm2.false_positive += 1
            elif gold and not fired:
                bm2.false_negative += 1
            else:
                bm2.true_negative += 1
        tp, fp, fn = bm2.true_positive, bm2.false_positive, bm2.false_negative
        bm2.precision = tp / (tp + fp) if (tp + fp) else 0.0
        bm2.recall = tp / (tp + fn) if (tp + fn) else 0.0
        summary.banner = bm2

    return summary
