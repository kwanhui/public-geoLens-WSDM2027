#!/usr/bin/env python3
"""Scoring of the WNUT-2016 evaluation set.

Every engine is run once over the full CSV and then scored only on the rows
where its task is well defined. Post-level engines are scored on the `intl`,
`hard-sem` and `ooc` buckets, where the ground truth is the post's own city.
User-level engines are scored on `userhome`, where it is the account's home
city. The `disagree` bucket is used for the cross-task banner alone, because
those rows carry two truths and a single-label accuracy over them would score
one level against the other's truth. Banner precision and recall are computed
over all rows, with the `disagree` rows as the positives.

The script also runs paired McNemar tests between engine pairs within each
section, and writes a results JSON holding the per-section metrics, the banner,
the significance tests and the run manifest.

What a run writes, beside the metrics:

- `manifest.engine_call_counts` (real, stub, failed and skipped calls per
  engine) and `manifest.engine_outcomes`, which splits each engine's
  attempted calls into answered, abstained, failed calls or unreadable
  replies, and LLM replies that named no catalogue place. `coverage` gives
  the same counts per scored section, with the denominator of every rate.
- A per-row CSV (`--rows-out`), one line per input row: the row's bucket and
  truths, and for each engine the place, the call outcome, whether it was
  right, and its distance from the truth; then each level's consensus and
  the verification flag under both consensus rules. It carries no tweet text.
- `flag`, the verification flag over the rows that carry a timeline
  (`disagree` and `userhome`), under the rule chosen with `--flag-consensus`
  (`weighted`, the default, or `majority`, one vote per engine) and the radius
  `--flag-radius-km`; `flag_comparison`, both rules at that radius; and
  `flag_sweep`, both rules at every radius in `--flag-radius-sweep`, also
  written as a CSV (`--sweep-out`). The comparison and the sweep re-apply the
  consensus to the predictions the run already holds, so they call no engine.

Run:
    python3 eval/adapters/run_wnut_eval.py \
        --csv eval/wnut2016_test_set.csv \
        --env .env \
        --out eval/results/wnut2016.json

`--env` is optional and points at whatever file holds your OPENAI_API_KEY and
ANTHROPIC_API_KEY; with the keys already in the environment, leave it out.

The committed results under eval/results/ were produced at tag
`wnut2016-eval-3803853`. A run at HEAD is a different run: see the errata at
the end of eval/README.md.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

POST_BUCKETS = {"intl", "hard-sem", "ooc"}
USER_BUCKETS = {"userhome"}
FLAG_BUCKETS = {"disagree", "userhome"}
POST_ENGINES = ["contrastgeo", "gazetteer_post", "gpt4o_mini_post", "claude_haiku_post"]
USER_ENGINES = ["fewuser", "retrievezero", "gazetteer_user", "gpt4o_mini_user", "claude_haiku_user"]
DEFAULT_SWEEP_KM = [25.0, 50.0, 100.0, 161.0, 250.0, 500.0, 1000.0, 2000.0, 5000.0]

# What an engine's call on one row came to. "answered" named a place;
# "abstained" returned and named none (the gazetteer finding no catalogue
# name); "failed" raised or returned a reply that could not be read;
# "no_catalogue_place" is an LLM reply that named nothing in the catalogue;
# "skipped" was never called (no text at its level); "not_run" means the row
# never reached the engines (out of catalogue, or a row error).
OUTCOMES = ("answered", "abstained", "failed", "no_catalogue_place", "skipped", "not_run")


def _bucket(row) -> str:
    from geolens.batch.metrics import bucket_of

    return bucket_of(row)


def outcome_of(row, engine: str) -> str:
    """One of OUTCOMES for one engine on one row."""
    from geolens.engines.base import NO_CATALOGUE_PLACE

    if row.status != "ok":
        return "not_run"
    pred = row.per_engine.get(engine)
    if pred is None or pred.skipped:
        return "skipped"
    if pred.outcome == NO_CATALOGUE_PLACE:
        return "no_catalogue_place"
    if pred.failed:
        return "failed"
    if pred.abstain or not pred.city:
        return "abstained"
    return "answered"


def engine_outcomes(rows: Iterable, engines: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per engine: how many rows, how many calls were attempted, and what they
    came to, with the share of attempted calls that named a place.

    `attempted` counts the calls made (answered, abstained, failed and
    no-catalogue-place replies). `failed_replies` is failed plus
    no-catalogue-place, the LLM replies that carried no usable answer.
    """
    rows = list(rows)
    out: dict[str, dict[str, Any]] = {}
    for engine in engines:
        c = dict.fromkeys(OUTCOMES, 0)
        for r in rows:
            c[outcome_of(r, engine)] += 1
        attempted = c["answered"] + c["abstained"] + c["failed"] + c["no_catalogue_place"]
        out[engine] = {
            "rows": len(rows),
            "attempted": attempted,
            **c,
            "failed_replies": c["failed"] + c["no_catalogue_place"],
            "coverage": round(c["answered"] / attempted, 4) if attempted else 0.0,
        }
    return out


def _truth(row, engine: str, granularities: Mapping[str, str]) -> str | None:
    from geolens.batch.metrics import truth_for

    return truth_for(row, granularities.get(engine))


def _correct(row, engine: str) -> bool | None:
    """True/False if engine's top-1 matches ground truth on this row; None if
    the engine was not run on the row, its call failed or the reply named no
    catalogue place, or the row has no ground truth. An abstention is a miss,
    as it is in Acc@1, so the paired test and the summary use the same rows."""
    gt = row.ground_truth_city
    pred = row.per_engine.get(engine)
    if not gt or pred is None or not pred.usable:
        return None
    return pred.city.lower() == gt.lower()


def _mcnemar(rows, a: str, b: str) -> dict:
    """Paired McNemar test (continuity-corrected) between two engines over the
    rows where both produced a usable result against a ground truth."""
    from geolens.stats import mcnemar

    b_only = c_only = paired = 0  # b_only: a right & b wrong; c_only: a wrong & b right
    for r in rows:
        ca, cb = _correct(r, a), _correct(r, b)
        if ca is None or cb is None:
            continue
        paired += 1
        if ca and not cb:
            b_only += 1
        elif cb and not ca:
            c_only += 1
    n = b_only + c_only
    if n == 0:
        return {"a": a, "b": b, "paired_rows": paired, "discordant": 0,
                "stat": 0.0, "p_value": 1.0}
    stat, p = mcnemar(b_only, c_only)
    return {"a": a, "b": b, "paired_rows": paired, "a_only_correct": b_only,
            "b_only_correct": c_only, "discordant": n, "stat": round(stat, 3),
            "p_value": round(p, 4)}


def retriangulate(row, granularities: Mapping[str, str], *, consensus: str, radius_km: float):
    """The row's consensus and flag under another rule or radius, from the
    predictions the row already holds. None for a row the engines never ran on."""
    from geolens.triangulator import triangulate

    if row.status != "ok" or not row.per_engine:
        return None
    return triangulate(row.per_engine, engines=granularities, radius_km=radius_km,
                       consensus=consensus)


def flag_metrics(rows: Iterable, granularities: Mapping[str, str], *,
                 consensus: str, radius_km: float) -> dict[str, Any]:
    """The verification flag over the labelled rows that carry a timeline.

    Every count is given with its denominator. `no_consensus` counts rows on
    which a level had no consensus place (every engine at that level failed
    or abstained), so the flag could not be raised; they stay in the
    denominators as rows on which it was not raised.
    """
    from geolens.stats import wilson_interval
    from geolens.triangulator import majority_tied

    tp = fp = fn = tn = no_consensus = ties = 0
    n_rows = 0
    for r in rows:
        if r.should_disagree is None or r.status != "ok" or not r.per_engine:
            continue
        tri = retriangulate(r, granularities, consensus=consensus, radius_km=radius_km)
        assert tri is not None
        n_rows += 1
        if not (tri.post_consensus_city and tri.user_consensus_city):
            no_consensus += 1
        if consensus == "majority" and (
            majority_tied(r.per_engine, granularities, "post")
            or majority_tied(r.per_engine, granularities, "user")
        ):
            ties += 1
        fired, gold = bool(tri.disagreement_flag), bool(r.should_disagree)
        if gold and fired:
            tp += 1
        elif fired:
            fp += 1
        elif gold:
            fn += 1
        else:
            tn += 1
    pos, neg = tp + fn, fp + tn

    def rate(k: int, n: int) -> dict[str, Any]:
        lo, hi = wilson_interval(k, n)
        return {"k": k, "n": n, "rate": round(k / n, 4) if n else 0.0,
                "wilson_95": [round(lo, 4), round(hi, 4)]}

    out: dict[str, Any] = {
        "consensus": consensus,
        "flag_radius_km": radius_km,
        "n_rows": n_rows,
        "n_positive": pos,
        "n_negative": neg,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "no_consensus": no_consensus,
        "true_positive_rate": rate(tp, pos),
        "false_positive_rate": rate(fp, neg),
        "precision": rate(tp, tp + fp),
    }
    if consensus == "majority":
        # Rows on which the vote at one level or both was tied, so the
        # confidences picked the place.
        out["tied_rows"] = ties
    return out


def flag_sweep(rows: Iterable, granularities: Mapping[str, str],
               radii: Iterable[float]) -> list[dict[str, Any]]:
    """`flag_metrics` under both consensus rules at every radius."""
    from geolens.triangulator import CONSENSUS_RULES

    rows = list(rows)
    return [flag_metrics(rows, granularities, consensus=rule, radius_km=float(km))
            for rule in CONSENSUS_RULES for km in radii]


SWEEP_COLUMNS = ["consensus", "flag_radius_km", "n_rows", "n_positive", "n_negative",
                 "true_positive", "false_positive", "false_negative", "true_negative",
                 "no_consensus", "true_positive_rate", "false_positive_rate", "precision"]


def write_sweep_csv(sweep: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SWEEP_COLUMNS)
        for m in sweep:
            w.writerow([m[c]["rate"] if isinstance(m[c], dict) else m[c]
                        for c in SWEEP_COLUMNS])


def section_of(row) -> str:
    """Which part of the evaluation a row belongs to."""
    b = _bucket(row)
    if row.status == "ooc":
        return "excluded_ooc"
    if b in POST_BUCKETS:
        return "post_level"
    if b in USER_BUCKETS:
        return "user_level"
    if b in FLAG_BUCKETS:
        return "flag_only"
    return "other"


def per_row_records(rows: Iterable, engine_names: list[str],
                    granularities: Mapping[str, str], *, radius_km: float) -> tuple[list[str], list[list[Any]]]:
    """Header and one record per row, for the per-row CSV. No input text."""
    from geolens.batch.metrics import _distance_eval
    from geolens.engines._coords import coords_for

    header = ["id", "bucket", "section", "status", "ground_truth_city",
              "ground_truth_user_city", "should_disagree"]
    for e in engine_names:
        header += [f"{e}_city", f"{e}_outcome", f"{e}_correct", f"{e}_error_km",
                   f"{e}_within_161km"]
    for rule in ("weighted", "majority"):
        header += [f"{rule}_post_consensus", f"{rule}_user_consensus",
                   f"{rule}_consensus_km", f"{rule}_flag"]
    records: list[list[Any]] = []
    for r in rows:
        rec: list[Any] = [r.id, _bucket(r), section_of(r), r.status,
                          r.ground_truth_city or "", r.ground_truth_user_city or "",
                          "" if r.should_disagree is None else int(bool(r.should_disagree))]
        for e in engine_names:
            outcome = outcome_of(r, e)
            pred = r.per_engine.get(e)
            truth = _truth(r, e, granularities)
            city = pred.city if (pred is not None and outcome == "answered") else ""
            correct: Any = ""
            err: Any = ""
            within: Any = ""
            if truth and outcome in ("answered", "abstained"):
                correct = int(bool(city) and city.lower() == truth.lower())
                if city:
                    km, ok = _distance_eval(city, truth)
                    err = "" if km is None else round(km, 1)
                    within = int(ok)
                elif coords_for(truth) is not None:
                    within = 0  # an abstention is a miss for Acc@161 km
            rec += [city, outcome, correct, err, within]
        for rule in ("weighted", "majority"):
            tri = retriangulate(r, granularities, consensus=rule, radius_km=radius_km)
            if tri is None:
                rec += ["", "", "", ""]
            else:
                km = "" if tri.disagreement_km is None else round(tri.disagreement_km, 1)
                rec += [tri.post_consensus_city, tri.user_consensus_city, km,
                        int(bool(tri.disagreement_flag))]
        records.append(rec)
    return header, records


def write_rows_csv(header: list[str], records: list[list[Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(records)


def _radii(text: str) -> list[float]:
    vals = [float(x) for x in text.split(",") if x.strip()]
    if not vals or any(not (1.0 <= v <= 20015.0) for v in vals):
        raise argparse.ArgumentTypeError("radii must be comma-separated km values in 1 to 20,015")
    return vals


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fair WNUT-2016 eval scorer")
    p.add_argument("--csv", default="eval/wnut2016_test_set.csv")
    p.add_argument(
        "--env",
        default=None,
        help=(
            "Path to a .env holding OPENAI_API_KEY and ANTHROPIC_API_KEY. "
            "Omit it when the keys are already in the environment."
        ),
    )
    p.add_argument("--out", default="eval/results/wnut2016.json")
    p.add_argument("--rows-out", default=None,
                   help="Per-row CSV (default: beside --out, with _rows.csv).")
    p.add_argument("--sweep-out", default=None,
                   help="Flag radius sweep CSV (default: beside --out, with _flag_sweep.csv).")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--ensemble-method", default="weighted", choices=["weighted", "rrf"])
    p.add_argument("--flag-consensus", default="weighted", choices=["weighted", "majority"],
                   help="Rule for each level's consensus behind the flag and the banner.")
    p.add_argument("--flag-radius-km", type=float, default=161.0)
    p.add_argument("--flag-radius-sweep", type=_radii, default=DEFAULT_SWEEP_KM,
                   help="Comma-separated radii in km for the flag sweep.")
    return p


def _beside(out: Path, suffix: str) -> Path:
    return out.with_name(out.stem + suffix)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.env:
        from dotenv import load_dotenv
        load_dotenv(args.env)

    # Imported after load_dotenv so the engines see the keys.
    from geolens.batch.metrics import compute_summary, gazetteer_abstentions, gazetteer_matches
    from geolens.batch.runner import run_batch
    from geolens.cli import _read_eval_csv
    from geolens.engines.registry import build_engines
    from geolens.manifest import build_manifest, call_counts

    # The same roster the server and the CLI run, from one registry.
    engines, catalogue = build_engines()
    granularities = {n: e.granularity for n, e in engines.items()}

    inputs = _read_eval_csv(args.csv)
    print(f"running {len(inputs)} rows x {len(engines)} engines ...", flush=True)
    batch = run_batch(inputs, engines, catalogue=catalogue, k=args.k,
                      ensemble_method=args.ensemble_method,
                      flag_radius_km=args.flag_radius_km)
    if args.flag_consensus != "weighted":
        for r in batch:
            tri = retriangulate(r, granularities, consensus=args.flag_consensus,
                                radius_km=args.flag_radius_km)
            if tri is not None:
                r.triangulation = tri

    post_rows = [r for r in batch if _bucket(r) in POST_BUCKETS]
    user_rows = [r for r in batch if _bucket(r) in USER_BUCKETS]
    flag_rows = [r for r in batch if _bucket(r) in FLAG_BUCKETS]

    post_summary = compute_summary(post_rows, catalogue_size=len(catalogue))
    user_summary = compute_summary(user_rows, catalogue_size=len(catalogue))
    full_summary = compute_summary(batch, catalogue_size=len(catalogue))

    def pick(summary, names):
        return {n: dataclasses.asdict(summary.per_engine[n]) for n in names if n in summary.per_engine}

    significance = {
        "post_level": [_mcnemar(post_rows, a, b)
                       for i, a in enumerate(POST_ENGINES) for b in POST_ENGINES[i + 1:]],
        "user_level": [_mcnemar(user_rows, a, b)
                       for i, a in enumerate(USER_ENGINES) for b in USER_ENGINES[i + 1:]],
    }

    all_names = list(engines)
    manifest = build_manifest(
        engines, catalogue, k=args.k, ensemble_method=args.ensemble_method,
        flag_radius_km=args.flag_radius_km,
        counts=call_counts([r.per_engine for r in batch if r.per_engine]),
        input_row_count=len(inputs),
        gazetteer_abstentions=gazetteer_abstentions(batch),
        gazetteer_matches=gazetteer_matches(batch),
    )
    manifest["flag_consensus"] = args.flag_consensus
    manifest["input_file_sha256"] = hashlib.sha256(Path(args.csv).read_bytes()).hexdigest()
    manifest["engine_outcomes"] = engine_outcomes(batch, all_names)

    sweep = flag_sweep(flag_rows, granularities, args.flag_radius_sweep)
    out = {
        "manifest": manifest,
        "counts": {
            "total": len(batch),
            "post_level_rows": len(post_rows),
            "userhome_rows": len(user_rows),
            "flag_rows": len(flag_rows),
            "ooc_rows": full_summary.ooc_rows,
            "error_rows": full_summary.error_rows,
        },
        "coverage": {
            "post_level": engine_outcomes([r for r in post_rows if r.status == "ok"], POST_ENGINES),
            "user_level": engine_outcomes(user_rows, USER_ENGINES),
            "flag_rows": engine_outcomes(flag_rows, all_names),
        },
        "post_level": {
            "engines": pick(post_summary, POST_ENGINES),
            "ensemble": dataclasses.asdict(post_summary.ensembles["post"]) if "post" in post_summary.ensembles else None,
            "per_bucket": {b: dataclasses.asdict(post_summary.per_bucket[b]) for b in post_summary.per_bucket},
        },
        "user_level": {
            "engines": pick(user_summary, USER_ENGINES),
            "ensemble": dataclasses.asdict(user_summary.ensembles["user"]) if "user" in user_summary.ensembles else None,
        },
        "banner": dataclasses.asdict(full_summary.banner) if full_summary.banner else None,
        "flag": flag_metrics(flag_rows, granularities, consensus=args.flag_consensus,
                             radius_km=args.flag_radius_km),
        "flag_comparison": [
            flag_metrics(flag_rows, granularities, consensus=rule, radius_km=args.flag_radius_km)
            for rule in ("weighted", "majority")
        ],
        "flag_sweep": sweep,
        "significance": significance,
    }

    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    rows_path = Path(args.rows_out) if args.rows_out else _beside(out_path, "_rows.csv")
    header, records = per_row_records(batch, all_names, granularities,
                                      radius_km=args.flag_radius_km)
    write_rows_csv(header, records, rows_path)
    sweep_path = Path(args.sweep_out) if args.sweep_out else _beside(out_path, "_flag_sweep.csv")
    write_sweep_csv(sweep, sweep_path)
    modes = sorted(set(out["manifest"]["engine_modes"].values()))
    print(f"wrote {args.out}, {rows_path} and {sweep_path}  (post_rows={len(post_rows)}, "
          f"userhome_rows={len(user_rows)}, ooc={full_summary.ooc_rows}, engine_modes={modes})",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
