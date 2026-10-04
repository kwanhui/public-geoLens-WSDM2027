"""Command-line interface: `python -m geolens.cli {geolocate,onboard,eval}`."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys

from geolens.dispatch import run_engines
from geolens.engines.base import Engine, GeolocateInput
from geolens.engines.registry import build_engines
from geolens.geo import ACC_KM_THRESHOLD
from geolens.onboarding import onboard_city
from geolens.triangulator import triangulate


def _cli_engines() -> tuple[dict[str, Engine], list[str]]:
    """The same roster the server runs.

    `geolens.engines.registry` is the one roster, so the CLI and the server
    cannot report different numbers for the same input.
    """
    return build_engines()


def _cmd_geolocate(args: argparse.Namespace) -> int:
    payload = GeolocateInput(post=args.post, user_handle=args.user_handle, user_posts=args.user_posts)
    engines, _ = _cli_engines()
    granularities = {n: e.granularity for n, e in engines.items()}
    per_engine = run_engines(engines, payload, k=args.k)
    tri = triangulate(per_engine, engines=granularities)

    out = {
        "consensus_city": tri.consensus_city,
        "consensus_confidence": tri.consensus_confidence,
        "agreement_score": tri.agreement_score,
        "disagreement_flag": tri.disagreement_flag,
        "notes": tri.notes,
        "per_engine": {
            n: {
                "city": p.city,
                "confidence": p.confidence,
                "top_k": p.top_k,
                "latency_ms": p.latency_ms,
                "cost_usd": p.cost_usd,
                "note": p.note,
            }
            for n, p in per_engine.items()
        },
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _cmd_onboard(args: argparse.Namespace) -> int:
    profile = onboard_city(args.city, region=args.region, force_refresh=args.refresh)
    json.dump(profile.__dict__, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v == "":
        return None
    return v in {"1", "true", "yes", "y", "t"}


def _read_eval_csv(path: str) -> list:
    """Parse a bulk-eval CSV into BatchInput rows (mirrors the server parser)."""
    from geolens.batch.runner import BatchInput

    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            up = (row.get("user_posts") or "").strip()
            rows.append(
                BatchInput(
                    id=(row.get("id") or str(i + 1)).strip(),
                    post=((row.get("post") or "").strip() or None),
                    user_posts=([s.strip() for s in up.split("|") if s.strip()] or None),
                    user_handle=((row.get("user_handle") or "").strip() or None),
                    ground_truth_city=((row.get("ground_truth_city") or "").strip() or None),
                    ground_truth_user_city=(
                        (row.get("ground_truth_user_city") or "").strip() or None
                    ),
                    bucket=((row.get("bucket") or row.get("tag") or "").strip() or None),
                    should_disagree=_parse_bool(row.get("should_disagree")),
                )
            )
    return rows


def _cmd_eval(args: argparse.Namespace) -> int:
    """Run the full engine roster over a CSV and emit the metrics summary.

    Without API keys (and unless --stub is passed) the LLM engines fall back to
    their offline stubs; the run manifest records each engine's real/stub mode,
    so stub runs are never mistaken for real numbers.
    """
    if args.stub:
        os.environ["GEOLENS_STUB_MODE"] = "1"

    from geolens.batch.metrics import compute_summary
    from geolens.batch.runner import run_batch
    from geolens.manifest import build_manifest, call_counts

    engines, catalogue = _cli_engines()

    inputs = _read_eval_csv(args.csv)
    if args.limit:
        inputs = inputs[: args.limit]
    batch = run_batch(
        inputs,
        engines,
        catalogue=catalogue,
        k=args.k,
        ensemble_method=args.ensemble_method,
        flag_radius_km=args.flag_radius_km,
    )
    granularities = {n: e.granularity for n, e in engines.items()}
    summary = compute_summary(
        batch, catalogue_size=len(catalogue), granularities=granularities
    )
    manifest = build_manifest(
        engines,
        catalogue,
        k=args.k,
        ensemble_method=args.ensemble_method,
        flag_radius_km=args.flag_radius_km,
        counts=call_counts([r.per_engine for r in batch if r.per_engine]),
    )

    out = {"manifest": manifest, "summary": dataclasses.asdict(summary)}
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        modes = set(manifest["engine_modes"].values())
        print(f"wrote {args.out}  (rows={summary.total_rows}, evaluated={summary.evaluated_rows}, "
              f"ooc={summary.ooc_rows}, engine_modes={sorted(modes)})")
    else:
        sys.stdout.write(text + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geolens.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("geolocate", help="Run all engines on one input.")
    g.add_argument("--post", default=None, help="Single post text.")
    g.add_argument("--user-handle", default=None)
    g.add_argument("--user-posts", nargs="*", default=None, help="Recent posts, space-separated.")
    g.add_argument("-k", type=int, default=5, help="Top-k cities to return per engine.")
    g.set_defaults(func=_cmd_geolocate)

    o = sub.add_parser("onboard", help="Generate a Modular Retrieval profile for a new city.")
    o.add_argument("--city", required=True)
    o.add_argument("--region", default="",
                   help="Country or region the place is in, e.g. 'Singapore'. Steers the "
                        "drafting prompt and is checked against the drafted coordinate.")
    o.add_argument("--refresh", action="store_true", help="Bypass cache.")
    o.set_defaults(func=_cmd_onboard)

    e = sub.add_parser("eval", help="Run all engines + ensembles over a bulk-eval CSV and print the metrics summary.")
    e.add_argument("csv", help="Path to a CSV (id, post, user_posts, ground_truth_city[, bucket, should_disagree]).")
    e.add_argument("-k", type=int, default=5, help="Top-k cities per engine.")
    e.add_argument("--ensemble-method", default="weighted", choices=["weighted", "rrf"])
    e.add_argument("--flag-radius-km", type=float, default=ACC_KM_THRESHOLD,
                   help="Separation at which the verification flag is raised "
                        f"(default {ACC_KM_THRESHOLD:.0f} km, the reported value).")
    e.add_argument("--stub", action="store_true", help="Force all engines into offline stub mode.")
    e.add_argument("--limit", type=int, default=0, help="Evaluate only the first N rows (0 = all).")
    e.add_argument("--out", default=None, help="Write the JSON summary+manifest to this path (default: stdout).")
    e.set_defaults(func=_cmd_eval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
