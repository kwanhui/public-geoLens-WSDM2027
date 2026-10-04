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
import dataclasses
import json

POST_BUCKETS = {"intl", "hard-sem", "ooc"}
USER_BUCKETS = {"userhome"}
POST_ENGINES = ["contrastgeo", "gazetteer_post", "gpt4o_mini_post", "claude_haiku_post"]
USER_ENGINES = ["fewuser", "retrievezero", "gazetteer_user", "gpt4o_mini_user", "claude_haiku_user"]


def _correct(row, engine: str) -> bool | None:
    """True/False if engine's top-1 matches ground truth on this row; None if
    the engine produced no prediction or the row has no ground truth."""
    gt = row.ground_truth_city
    pred = row.per_engine.get(engine)
    if not gt or pred is None:
        return None
    return pred.city.lower() == gt.lower()


def _mcnemar(rows, a: str, b: str) -> dict:
    """Paired McNemar test (continuity-corrected) between two engines over the
    rows where both produced a prediction against a ground truth."""
    from geolens.stats import mcnemar

    b_only = c_only = 0  # b_only: a right & b wrong; c_only: a wrong & b right
    for r in rows:
        ca, cb = _correct(r, a), _correct(r, b)
        if ca is None or cb is None:
            continue
        if ca and not cb:
            b_only += 1
        elif cb and not ca:
            c_only += 1
    n = b_only + c_only
    if n == 0:
        return {"a": a, "b": b, "discordant": 0, "stat": 0.0, "p_value": 1.0}
    stat, p = mcnemar(b_only, c_only)
    return {"a": a, "b": b, "a_only_correct": b_only, "b_only_correct": c_only,
            "discordant": n, "stat": round(stat, 3), "p_value": round(p, 4)}


def main(argv: list[str] | None = None) -> int:
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
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--ensemble-method", default="weighted", choices=["weighted", "rrf"])
    args = p.parse_args(argv)

    if args.env:
        from dotenv import load_dotenv
        load_dotenv(args.env)

    # Imported after load_dotenv so the engines see the keys.
    from geolens.batch.metrics import bucket_of, compute_summary
    from geolens.batch.runner import run_batch
    from geolens.cli import _read_eval_csv
    from geolens.engines.registry import build_engines
    from geolens.manifest import build_manifest

    # The same roster the server and the CLI run, from one registry.
    engines, catalogue = build_engines()

    inputs = _read_eval_csv(args.csv)
    print(f"running {len(inputs)} rows x {len(engines)} engines ...", flush=True)
    batch = run_batch(inputs, engines, catalogue=catalogue, k=args.k, ensemble_method=args.ensemble_method)

    post_rows = [r for r in batch if bucket_of(r) in POST_BUCKETS]
    user_rows = [r for r in batch if bucket_of(r) in USER_BUCKETS]

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

    out = {
        "manifest": build_manifest(engines, catalogue, k=args.k, ensemble_method=args.ensemble_method),
        "counts": {
            "total": len(batch),
            "post_level_rows": len(post_rows),
            "userhome_rows": len(user_rows),
            "ooc_rows": full_summary.ooc_rows,
            "error_rows": full_summary.error_rows,
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
        "significance": significance,
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    modes = sorted(set(out["manifest"]["engine_modes"].values()))
    print(f"wrote {args.out}  (post_rows={len(post_rows)}, userhome_rows={len(user_rows)}, "
          f"ooc={full_summary.ooc_rows}, engine_modes={modes})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
