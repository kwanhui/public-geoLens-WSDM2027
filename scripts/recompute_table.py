#!/usr/bin/env python3
"""Recompute every number the paper prints, from the committed files alone.

Point ``--paper-dir`` (or the ``GEOLENS_PAPER_DIR`` environment variable) at a
copy of the paper's LaTeX sections. Each check then reads the value out of the
LaTeX and compares it with a recomputation from ``eval/results/``, the
committed tweet-ID manifest, the WNUT-2016 adapter, the example set, the
built-in coordinates, the Dockerfile and the newest recorded scenario check.
Without the LaTeX the paper-side comparisons are skipped and the rest still
runs. A value the paper does not print is skipped with a note rather than
failing. The script runs no engine, calls no API, and exits non-zero when
anything checked mismatches. ``eval/README.md`` has the protocol and the
errata.

Run:
    python3 scripts/recompute_table.py --paper-dir path/to/paper/sections
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import subprocess
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations
from pathlib import Path

from geolens.engines._coords import CITY_COORDS
from geolens.geo import ACC_KM_THRESHOLD, haversine_km
from geolens.stats import mcnemar, wilson_interval

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results"
ID_MANIFEST = REPO / "eval" / "wnut2016_id_manifest.csv"
ADAPTER_PATH = REPO / "eval" / "adapters" / "wnut2016_to_geolens.py"
EXAMPLE_SET = REPO / "eval" / "example_test_set.csv"
SCENARIO_CHECKS = REPO / "docs" / "scenario-checks"
DOCKERFILE = REPO / "Dockerfile"
SERVER = REPO / "src" / "geolens" / "ui" / "server.py"
# The paper's LaTeX sections are not part of this repository.
PAPER_SECTIONS = os.environ.get("GEOLENS_PAPER_DIR") or None

# The engines the paper's table names, in the order its rows appear. A row is
# matched on the substring, so renaming "ContrastGeo (frozen)" to
# "SimCSE-BERT (ContrastGeo)" does not move the check.
TABLE_ROW_KEYS = [
    ("ContrastGeo", "contrastgeo"),
    ("FewUser", "fewuser"),
    ("RetrieveZero", "retrievezero"),
    ("Gazetteer", "gazetteer_{level}"),
    ("GPT-4o-mini", "gpt4o_mini_{level}"),
    ("Claude Haiku", "claude_haiku_{level}"),
    ("Fusion", "fusion"),
]

LLM_ENGINES = {"gpt4o_mini_post", "gpt4o_mini_user", "claude_haiku_post", "claude_haiku_user"}

# Stated in the paper, reproducible from no committed file.
NOT_CHECKABLE = [
    "the verification flag's 161 km separation radius as actually used for "
    "the two runs: no manifest in eval/results/ carries a flag_radius_km "
    "field, only the README's description of the API default",
    "the 22 seed places against the 28 most frequent WNUT-2016 cities: "
    "inferable only from a comment boundary in "
    "src/geolens/engines/_cities.py, not a structured field in any "
    "committed result file",
]

NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
    20: "twenty",
}


# ---------- formatting, in the units the paper prints ----------

def _round(value: float, places: int) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def acc2(value: float) -> str:
    """Two decimals with the leading zero dropped, as the table prints it.

    Rounds half away from zero. A proportion over a round denominator lands on
    an exact .xx5 often enough here (35/200, 123/200) that the tie-breaking
    rule decides two of the table's cells, and printf on a binary double would
    break those ties downward.
    """
    return str(_round(value, 2)).lstrip("0")


def acc3(value: float) -> str:
    """Three decimals with the leading zero dropped, as the per-bucket and
    run-variation prose prints Acc@1 (".898", ".585")."""
    return str(_round(value, 3)).lstrip("0")


def rate2(value: float) -> str:
    """Two decimals with the leading zero kept, as the flag prose prints its
    rates ("0.78", "0.60")."""
    return str(_round(value, 2))


def ratio1(value: float) -> str:
    """One decimal, as a cost ratio prints ("8.2")."""
    return str(_round(value, 1))


def is_rounding_tie(value: float, places: int = 2) -> bool:
    """True when the value sits exactly on the rounding boundary at `places`."""
    unit = Decimal(1).scaleb(-places)
    return Decimal(repr(value)).quantize(unit / 10) % unit == unit / 2


def interval(successes: int, n: int) -> str:
    low, high = wilson_interval(successes, n)
    return f"[{acc2(low)}, {acc2(high)}]"


def wilson_range_rate2(successes: int, n: int) -> str:
    """A Wilson interval in the flag prose's "x to y" phrasing."""
    low, high = wilson_interval(successes, n)
    return f"{rate2(low)} to {rate2(high)}"


def latency(ms: float) -> str:
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def words(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


def _tie_note(value: float, places: int = 2) -> str:
    return f"(exactly {value:g}, rounded up)" if is_rounding_tie(value, places) else ""


# ---------- the Claude rate the runs were costed at ----------
# The adapter priced claude-haiku-4-5-20251001 at USD 0.80 / MTok input and
# 4.00 output, which is the Claude Haiku 3.5 price; the list price for 4.5 is
# 1.00 and 5.00. Both rates were low by the same factor, so every Claude cost
# in eval/results/ is exactly 0.8 of the correct figure. The result files
# record what the run computed and are not edited. The correction is applied
# here, and the script prints the recorded value, the factor and the
# corrected value side by side.
CLAUDE_COST_CORRECTION = 1.25
CLAUDE_COST_CORRECTION_WHY = (
    "the run costed Claude Haiku 4.5 at the Haiku 3.5 rate (USD 0.80/4.00 per "
    "MTok); the list price is 1.00/5.00, so the recorded cost is multiplied by "
    f"{CLAUDE_COST_CORRECTION}"
)


def cost_correction(engine: str) -> float:
    """The factor a recorded total_cost_usd is multiplied by, per engine."""
    return CLAUDE_COST_CORRECTION if engine.startswith("claude_haiku") else 1.0


def corrected_cost(engine: str, total_usd: float) -> float:
    return total_usd * cost_correction(engine)


def cost_per_1000(total_usd: float, n: int) -> str:
    per_1000 = total_usd / n * 1000
    return "0" if per_1000 == 0 else f"{per_1000:.2f}"


# ---------- the paper ----------

def paper_text(directory: Path) -> str | None:
    """Every section of the manuscript as one whitespace-normalised string."""
    if not directory.is_dir():
        return None
    parts = [p.read_text() for p in sorted(directory.glob("*.tex"))]
    if not parts:
        return None
    return " ".join(" ".join(parts).split())


class Report:
    """Each line compares what the paper prints with what the files give.

    A check whose pattern does not match the paper is skipped with a note, so
    a figure the paper no longer prints does not fail the run.
    """

    def __init__(self, paper: str | None) -> None:
        self.paper = paper
        self.mismatches: list[str] = []
        self.skipped: list[str] = []

    def _print(self, label: str, paper: str, recomputed: str, note: str) -> None:
        ok = paper == recomputed
        if not ok:
            self.mismatches.append(label)
        print(f"  {label:<56} paper={paper:<14} recomputed={recomputed:<14} "
              f"{'OK' if ok else 'MISMATCH'}{'  ' + note if note else ''}")

    def skip(self, label: str, recomputed: str, why: str) -> None:
        self.skipped.append(label)
        print(f"  {label:<56} skipped: {why} (recomputed={recomputed})")

    def check(self, label: str, paper: str, recomputed: str, note: str = "") -> None:
        """Compare a value transcribed from the paper against a recomputation."""
        self._print(label, paper, recomputed, note)

    def from_paper(self, label: str, pattern: str, recomputed: str,
                   note: str = "", group: int = 1) -> None:
        """Read the paper's own value with `pattern` and compare it."""
        if self.paper is None:
            self.skip(label, recomputed, "no paper source")
            return
        match = re.search(pattern, self.paper)
        if match is None:
            self.skip(label, recomputed, "the paper no longer prints this")
            return
        self._print(label, match.group(group), recomputed, note)


# ---------- inputs other than eval/results ----------

def _load_adapter():
    """Import the WNUT-2016 adapter for its IN_CAT_KM and DEFAULT_TARGETS."""
    spec = importlib.util.spec_from_file_location("wnut2016_to_geolens", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_rows() -> list[dict[str, str]]:
    with ID_MANIFEST.open(newline="") as f:
        return list(csv.DictReader(f))


def _id_manifest_bucket_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in _manifest_rows():
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    return counts


def _majority_class_rate(bucket: str) -> tuple[str, float]:
    """The most frequent ground-truth place in a bucket, and its share."""
    counts = Counter(r["ground_truth_city"] for r in _manifest_rows() if r["bucket"] == bucket)
    total = sum(counts.values())
    place, hits = counts.most_common(1)[0]
    return place, hits / total


def _dockerfile_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in DOCKERFILE.read_text().splitlines():
        match = re.match(r"\s*ENV\s+([A-Z0-9_]+)=(\S+)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _max_batch_rows_default() -> str:
    """The package default for MAX_BATCH_ROWS, which the Dockerfile leaves alone."""
    match = re.search(r'_int_env\("MAX_BATCH_ROWS",\s*(\d+)\)', SERVER.read_text())
    return match.group(1) if match else "?"


def _newest_scenario_check() -> Path:
    files = sorted(SCENARIO_CHECKS.glob("*.json"))
    if not files:
        raise SystemExit("no scenario-check files found under docs/scenario-checks/")
    return files[-1]


# ---------- the table ----------

def parse_paper_table(paper: str | None) -> dict[str, dict[str, object]] | None:
    """The results table as the manuscript prints it, keyed by engine.

    Reading the rows rather than transcribing them means a relabelled engine
    or an edited cell is compared as it now stands.
    """
    if paper is None:
        return None
    start = paper.find(r"\label{tab:results}")
    end = paper.find(r"\bottomrule", start)
    if start < 0 or end < 0:
        return None
    block = paper[start:end]
    levels: dict[str, dict[str, object]] = {}
    level = ""
    for raw in block.split(r"\\"):
        header = re.search(r"\\emph\{(Post|User) level\} \(\$n=(\d+)\$", raw)
        if header:
            level = header.group(1).lower()
            levels[level] = {"n": int(header.group(2)), "rows": {}}
            continue
        if not level or "&" not in raw:
            continue
        cells = [c.strip() for c in raw.split("&")]
        if len(cells) != 5:
            continue
        label = cells[0].lstrip("\\midrule").strip()
        key = next((k for name, k in TABLE_ROW_KEYS if name in label), "")
        if not key:
            continue
        acc1 = re.match(r"(\.\d+) (\[[^\]]*\])", cells[1])
        if not acc1:
            continue
        row = {"acc1": acc1.group(1), "ci": acc1.group(2), "acc161": cells[2]}
        if cells[3] != "--":
            row["latency"] = cells[3].replace("\\,", " ")
            row["cost"] = cells[4]
        rows = levels[level]["rows"]
        assert isinstance(rows, dict)
        rows[key.format(level=level)] = row
    return levels or None


def check_table(report: Report, result: dict, table: dict | None) -> None:
    if table is None:
        print("\n[table] skipped: the paper's results table could not be read")
        return
    for level, spec in table.items():
        n = int(spec["n"])
        section = result[f"{level}_level"]
        print(f"\n[table: {level} level, n={n}]")
        for engine, paper in spec["rows"].items():
            metrics = section["ensemble"] if engine == "fusion" else section["engines"][engine]
            if metrics["n_evaluated"] != n:
                raise SystemExit(
                    f"{engine}: the run scored {metrics['n_evaluated']} rows, "
                    f"but the paper's {level}-level block says n={n}"
                )
            successes = round(metrics["acc_at_1"] * n)
            report.check(f"{engine} Acc@1", paper["acc1"], acc2(metrics["acc_at_1"]),
                         _tie_note(metrics["acc_at_1"]))
            report.check(f"{engine} Acc@1 95% Wilson", paper["ci"], interval(successes, n))
            report.check(f"{engine} Acc@161km", paper["acc161"], acc2(metrics["acc_at_161km"]),
                         _tie_note(metrics["acc_at_161km"]))
            if "latency" in paper:
                report.check(f"{engine} median latency", paper["latency"],
                             latency(metrics["median_latency_ms"]))
                recorded = metrics["total_cost_usd"]
                factor = cost_correction(engine)
                note = ""
                if factor != 1.0:
                    note = (f"(recorded {recorded:.7g} USD x {factor}: "
                            f"{CLAUDE_COST_CORRECTION_WHY})")
                report.check(f"{engine} cost per 1,000 rows", paper["cost"],
                             cost_per_1000(corrected_cost(engine, recorded), n), note)


# ---------- the evaluation prose ----------

def check_prose(report: Report, result: dict) -> None:
    counts = result["counts"]
    print("\n[prose: the row sets]")
    report.from_paper("total rows", r"an ([\d,]+)-row (?:sub)?set", str(counts["total"]))
    report.from_paper("rows sent to the engines",
                      r"([\d,]+) catalogued rows",
                      str(counts["total"] - counts["ooc_rows"]))
    report.from_paper("out-of-catalogue rows", r"(\d+) tweets lie beyond",
                      str(counts["ooc_rows"]))
    report.from_paper("paired rows", r"The (\d+) \\emph\{paired\} rows",
                      str(counts["total"] - counts["post_level_rows"] - counts["userhome_rows"]))
    # The paper gives the catalogue as its two groups, so the size is their sum.
    size = str(result["manifest"]["catalogue_size"])
    groups = re.search(r"catalogue contains (\d+) frequent [\w-]+ cities and (\d+) seed places",
                       report.paper or "")
    if report.paper is None:
        report.skip("catalogue size N", size, "no paper source")
    elif groups is None:
        report.skip("catalogue size N", size, "the paper no longer prints this")
    else:
        cities, seeds = int(groups.group(1)), int(groups.group(2))
        report.check("catalogue size N", str(cities + seeds), size,
                     f"(paper: {cities} cities and {seeds} seed places)")

    # No per-engine metric section exists for the out-of-catalogue rows.
    post_buckets = sorted(result["post_level"]["per_bucket"].keys())
    user_has_per_bucket = "per_bucket" in result["user_level"]
    ooc_present = "ooc" in post_buckets or (
        user_has_per_bucket and "ooc" in result["user_level"]["per_bucket"]
    )
    print(f"  post_level per_bucket keys: {post_buckets}")
    report.check("out-of-catalogue rows carry no per-engine metric section",
                 "absent", "present" if ooc_present else "absent")

    print("\n[prose: paired significance tests]")
    pairs = {(t["a"], t["b"]): t for level in ("post_level", "user_level")
             for t in result["significance"][level]}

    def p_for(a: str, b: str) -> float:
        test = pairs[(a, b)]
        _stat, p = mcnemar(test["a_only_correct"], test["b_only_correct"])
        return p

    report.from_paper("McNemar, the two LLMs at post level",
                      r"\(\$p=([\d.]+)\$ for posts",
                      f"{p_for('gpt4o_mini_post', 'claude_haiku_post'):.2f}")
    report.from_paper("McNemar, the two LLMs at user level",
                      r"and \$p=([\d.]+)\$ for users\)",
                      f"{p_for('gpt4o_mini_user', 'claude_haiku_user'):.2f}")
    cross = [p_for(a, b) for (a, b) in pairs if (a in LLM_ENGINES) != (b in LLM_ENGINES)]
    report.from_paper("McNemar, every LLM against every encoder and the gazetteer",
                      r"from the encoders and the gazetteer \(\$p<([\d.]+)\$\)",
                      "0.001" if all(p < 0.001 for p in cross) else "not all below 0.001",
                      f"({len(cross)} pairs, largest p = {max(cross):.2e})")

    print("\n[not reported in the paper: fusion's differs-from-best-single rate]")
    for level in ("post", "user"):
        rate = result[f"{level}_level"]["ensemble"]["differs_from_best_single_rate"]
        print(f"  {level:<10} {rate * 100:.0f} %")


def check_buckets(report: Report, result: dict) -> None:
    print("\n[prose: per-bucket post-level Acc@1]")
    per_bucket = result["post_level"]["per_bucket"]
    named, unnamed = per_bucket["intl"], per_bucket["hard-sem"]

    report.from_paper("rows whose text names a catalogue place",
                      r"On the (\d+) rows on which its matcher finds a catalogue place",
                      str(named["n_rows"]))
    report.from_paper("rows whose text names none", r"On the other (\d+) rows",
                      str(unnamed["n_rows"]))

    for engine in ("gazetteer_post", "gpt4o_mini_post", "claude_haiku_post"):
        report.from_paper(f"names a place, Acc@1, {engine}",
                          r"each reach an Acc@1 of (\.\d+)",
                          acc3(named["acc_at_1"][engine]),
                          _tie_note(named["acc_at_1"][engine], places=3))
    report.from_paper("names a place, Acc@1, contrastgeo",
                      r"and ContrastGeo reaches (\.\d+)\.",
                      acc3(named["acc_at_1"]["contrastgeo"]),
                      _tie_note(named["acc_at_1"]["contrastgeo"], places=3))

    report.from_paper("names none, Acc@1, gazetteer_post",
                      r"the gazetteer scores 0 by construction \((\.\d+) in the run",
                      acc3(unnamed["acc_at_1"]["gazetteer_post"]))
    report.from_paper("names none, Acc@1, contrastgeo",
                      r"and ContrastGeo reaches (\.\d+), whereas",
                      acc3(unnamed["acc_at_1"]["contrastgeo"]))
    report.from_paper("names none, Acc@1, gpt4o_mini_post",
                      r"whereas GPT-4o-mini reaches (\.\d+)",
                      acc3(unnamed["acc_at_1"]["gpt4o_mini_post"]))
    report.from_paper("names none, Acc@1, claude_haiku_post",
                      r"and Claude Haiku 4\.5 reaches (\.\d+)\.",
                      acc3(unnamed["acc_at_1"]["claude_haiku_post"]))

    print("\n[prose: the majority-class baseline]")
    place_unnamed, rate_unnamed = _majority_class_rate("hard-sem")
    place_user, rate_user = _majority_class_rate("userhome")
    report.from_paper("majority-class place",
                      r"The majority-class baseline \((\w[\w ]*)\) is",
                      place_unnamed if place_unnamed == place_user else
                      f"{place_unnamed}/{place_user}")
    report.from_paper("majority-class rate, rows naming no place",
                      r"majority-class baseline \([\w ]+\) is (\.\d+) on these rows",
                      acc3(rate_unnamed))
    report.from_paper("majority-class rate, user-level rows",
                      r"and (\.\d+) on the user-level rows",
                      acc3(rate_user))

    print("\n[not reported in the paper: the rest of each bucket's per-engine Acc@1]")
    for name, bucket in per_bucket.items():
        best = max(bucket["acc_at_1"].items(), key=lambda kv: kv[1])
        print(f"  {name:<10} n={bucket['n_rows']:<4} best {best[0]} at {acc2(best[1])}")


def check_costs(report: Report, result: dict) -> None:
    print("\n[prose: Claude Haiku 4.5 against GPT-4o-mini on cost]")
    print(f"  correction applied to the two Claude engines: {CLAUDE_COST_CORRECTION_WHY}.")
    for level, label in (("post_level", "post"), ("user_level", "user")):
        engines = result[level]["engines"]
        gpt = engines[f"gpt4o_mini_{label}"]["total_cost_usd"]
        recorded = engines[f"claude_haiku_{label}"]["total_cost_usd"]
        claude = corrected_cost(f"claude_haiku_{label}", recorded)
        # The paper prints the ratio rounded to a whole number, in words.
        ratio = words(int(_round(claude / gpt, 0)))
        pattern = (r"costs (?:approximately )?(\w+) and \w+ times" if label == "post"
                   else r"costs (?:approximately )?\w+ and (\w+) times")
        report.from_paper(f"cost ratio, Claude to GPT-4o-mini, {label} level", pattern, ratio,
                          f"(ratio {ratio1(claude / gpt)}; Claude recorded {recorded:.7g} USD, "
                          f"corrected {claude:.7g}; GPT-4o-mini {gpt:.7g} unchanged)")


def check_run_variation(report: Report, result: dict, banner: dict) -> None:
    print("\n[prose: run-to-run variation, GPT-4o-mini user-level Acc@1]")
    first = result["user_level"]["engines"]["gpt4o_mini_user"]["acc_at_1"]
    second = banner["user_level"]["engines"]["gpt4o_mini_user"]["acc_at_1"]
    report.from_paper("GPT-4o-mini user-level Acc@1, first run",
                      r"changes from (\.\d+) to \.\d+", acc3(first))
    report.from_paper("GPT-4o-mini user-level Acc@1, second run",
                      r"changes from \.\d+ to (\.\d+)", acc3(second))


def check_flag(report: Report, flag_rescore: dict, result: dict) -> None:
    print("\n[prose: verification flag, 400-row user-timeline set]")
    row = next(e for e in flag_rescore["row_sets"] if e["row_set"] == "user_timeline_400")
    tp, fp = row["true_positive"], row["false_positive"]
    fn, tn = row["false_negative"], row["true_negative"]
    n_positive, n_negative = tp + fn, fp + tn
    tpr, fpr = tp / n_positive, fp / n_negative

    report.from_paper("flag rows (user-timeline set)",
                      r"over the (\d+) rows with a timeline", str(row["n_rows"]))
    report.from_paper("flag true positives", r"raised on (\d+) of the \d+ paired rows", str(tp))
    report.from_paper("paired rows (flag positives)",
                      r"raised on \d+ of the (\d+) paired rows", str(n_positive))
    report.from_paper("flag true-positive rate (Section 5 and abstract)",
                      r"a true-positive rate of ([\d.]+)", rate2(tpr))
    report.from_paper("flag true-positive rate 95% Wilson",
                      r"Wilson interval ([\d.]+ to [\d.]+)\)",
                      wilson_range_rate2(tp, n_positive))
    report.from_paper("flag false positives",
                      r"on (\d+) of the \d+ home-consistent rows", str(fp))
    report.from_paper("home-consistent rows (flag negatives)",
                      r"on \d+ of the (\d+) home-consistent rows", str(n_negative))
    report.from_paper("flag false-positive rate (Section 5 and abstract)",
                      r"a false-positive rate of ([\d.]+)", rate2(fpr))
    report.from_paper("flag false-positive rate 95% Wilson",
                      r"a false-positive rate of [\d.]+ \(([\d.]+ to [\d.]+)\)",
                      wilson_range_rate2(fp, n_negative))
    report.from_paper("flag precision", r"precision of ([\d.]+) \(", rate2(tp / (tp + fp)))
    report.from_paper("flag precision 95% Wilson",
                      r"precision of [\d.]+ \(([\d.]+ to [\d.]+)\)",
                      wilson_range_rate2(tp, tp + fp))

    prevalence = 0.05
    adjusted = (prevalence * tpr) / (prevalence * tpr + (1 - prevalence) * fpr)
    report.from_paper("flag precision at 5% away-from-home prevalence",
                      r"precision would be approximately ([\d.]+)", rate2(adjusted),
                      f"(formula: 0.05*TPR / (0.05*TPR + 0.95*FPR) = "
                      f"0.05*{tpr:.4f} / (0.05*{tpr:.4f} + 0.95*{fpr:.4f}) = {adjusted:.4f})")

    print("\n[for reference: not reported in the paper, the 737-row catalogued set]")
    prescribed = next(e for e in flag_rescore["row_sets"] if e["row_set"] == "catalogued_737")
    banner = result["banner"]
    if banner["n_labelled"] != prescribed["n_rows"]:
        raise SystemExit("wnut2016.json's banner and wnut2016_flag_rescore.json's "
                         "catalogued_737 entry disagree on the row count")
    tp737, fp737, fn737 = (banner["true_positive"], banner["false_positive"],
                           banner["false_negative"])
    print(f"  flag rows: {banner['n_labelled']}  positives: {banner['n_positive']}")
    print(f"  precision: {rate2(tp737 / (tp737 + fp737))} "
          f"[{wilson_range_rate2(tp737, tp737 + fp737)}]")
    print(f"  recall:    {rate2(tp737 / (tp737 + fn737))} "
          f"[{wilson_range_rate2(tp737, tp737 + fn737)}]")


def check_gazetteer_artefacts(report: Report, result: dict) -> None:
    """The hits the abstention fix may remove from the committed gazetteer rows."""
    print("\n[prose: the abstaining gazetteer's chance hits]")
    user = result["user_level"]["engines"]["gazetteer_user"]
    post = result["post_level"]["engines"]["gazetteer_post"]
    n_user = user["n_evaluated"]
    n_post = post["n_evaluated"]
    report.from_paper("gazetteer user-level Acc@1 matches",
                      r"gazetteer's (\d+) user-level Acc@1 matches",
                      str(round(user["acc_at_1"] * n_user)))
    report.from_paper("gazetteer user-level matches within 161 km",
                      r"at most \d+ of its (\d+) user-level matches within 161",
                      str(round(user["acc_at_161km"] * n_user)))
    without_four = round(post["acc_at_161km"] * n_post) - 4
    report.from_paper("gazetteer post-level Acc@161km without four chance hits",
                      r"its post-level Acc@161~km is (\.\d+) \(\d+ of \d+\)",
                      acc2(without_four / n_post))
    report.from_paper("gazetteer post-level hits within 161 km without four",
                      r"post-level Acc@161~km is \.\d+ \((\d+) of \d+\)", str(without_four))
    report.from_paper("gazetteer post-level rows behind that rate",
                      r"post-level Acc@161~km is \.\d+ \(\d+ of (\d+)\)", str(n_post))


def check_catalogue_geography(report: Report) -> None:
    """What Acc@161 km cannot separate, computed from the built-in coordinates."""
    print("\n[prose: places Acc@161km cannot tell apart, from engines/_coords.py]")
    singapore = CITY_COORDS["Singapore"]
    cluster = {name for name, point in CITY_COORDS.items()
               if haversine_km(singapore, point) <= ACC_KM_THRESHOLD}
    pairs = [(a, b) for a, b in combinations(sorted(CITY_COORDS), 2)
             if haversine_km(CITY_COORDS[a], CITY_COORDS[b]) <= ACC_KM_THRESHOLD]
    other = [p for p in pairs if not (p[0] in cluster and p[1] in cluster)]
    report.from_paper("Singapore places within 161 km of each other",
                      r"cannot distinguish the (\w+) Singapore places", words(len(cluster)))
    report.from_paper("other city pairs within 161 km",
                      r"Singapore places or (\w+) (?:pairs of nearby cities|nearby city pairs)",
                      words(len(other)))
    print(f"  the pairs: {', '.join(f'{a} and {b}' for a, b in other)}")


def check_hosted_limits(report: Report) -> None:
    """What the hosted instance enforces, read from the Dockerfile and the defaults."""
    print("\n[prose: the hosted instance's limits, from the Dockerfile]")
    env = _dockerfile_env()
    report.from_paper("hosted queries per hour",
                      r"\((\d+) queries, \d+ profile saves", env.get("MAX_QUERIES_PER_HOUR", "?"))
    report.from_paper("hosted profile saves per hour",
                      r"queries, (\d+) profile saves", env.get("MAX_PROFILE_SAVES_PER_HOUR", "?"))
    report.from_paper("hosted bulk jobs per hour",
                      r"profile saves, and (\d+) bulk jobs", env.get("MAX_BATCHES_PER_HOUR", "?"))
    report.from_paper("hosted rows per bulk job",
                      r"bulk jobs of at most (\d+) rows", _max_batch_rows_default())
    report.from_paper("hosted onboarded places",
                      r"at most (\d+) (?:such|onboarded) places", env.get("GEOLENS_MAX_ONBOARDED", "?"))
    ttl = env.get("GEOLENS_ONBOARD_TTL_MINUTES", "0")
    hours = int(ttl) // 60 if ttl.isdigit() else 0
    report.from_paper("hosted expiry of an onboarded place",
                      r"(?:such|onboarded) places for (\w+) hour", words(hours))


# ---------- the demonstration ----------

def check_example_set(report: Report) -> None:
    print("\n[prose: the Demonstration section]")
    with EXAMPLE_SET.open(newline="") as f:
        n = sum(1 for _ in csv.DictReader(f))
    report.from_paper("authored example file rows",
                      r"run the (\d+)-row authored example file", str(n))


def _scenario_files_changed_since(commit: str) -> list[str]:
    """Scenario files committed after `commit`, so a record may be stale.

    The bundled records under `scenarios/records/` are written from the check
    itself, so they move with it and are not evidence of drift.
    """
    if not commit:
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--name-only", f"{commit}..HEAD", "--",
             "src/geolens/ui/static/scenarios"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in out.splitlines()
            if line.strip() and "/scenarios/records/" not in line]


def check_scenario_checks(report: Report) -> None:
    path = _newest_scenario_check()
    data = json.loads(path.read_text())
    commit = data.get("tool", {}).get("commit", "")
    dirty = data.get("tool", {}).get("dirty")
    print(
        f"  newest scenario check: {path.name}  "
        f"tool commit: {commit or '<no tool.commit field>'}{'  (dirty)' if dirty else ''}"
    )

    changed = _scenario_files_changed_since(commit)
    if changed:
        print(f"  warning: {len(changed)} scenario file(s) changed after that commit, so "
              f"{path.name} may describe an older scenario. Re-record with "
              "scripts/check_scenarios.py against a deployed instance.")
        for name in changed:
            print(f"    - {name}")

    report.from_paper("scenario presets", r"selects one of (\w+) scenario presets",
                      words(len(data["scenarios"])))

    scenario = next((s for s in data["scenarios"] if s["id"] == "osint-credibility"), None)
    if scenario is None:
        raise SystemExit(f"{path.name}: no 'osint-credibility' scenario recorded")
    # The preset walks the post alone, the account alone and then the two
    # together; only the last of those puts one level against the other, so
    # it is the entry the flag, the distance and the votes come from.
    query = next((q for q in scenario["posts"] if q.get("post") and q.get("user_posts")), None)
    if query is None:
        raise SystemExit(
            f"{path.name}: no 'osint-credibility' query sends both a post and a timeline")
    km = query["flag"]["km"]
    report.from_paper("OSINT scenario check separation (figure caption)",
                      r"return Tokyo, ([\d,]+)~km away", f"{round(km):,}")

    notes_text = " ".join(query["flag"].get("notes", []))
    causes = ["one of the two predictions is wrong", "the post is about another place",
              "a travel post", "a shared or compromised account", "a misleading geotag"]
    found = [c for c in causes if c in notes_text]
    report.from_paper("verification flag candidate causes",
                      r"the distance, and (\w+) (?:candidate|possible) causes", words(len(found)),
                      "" if len(found) == len(causes) else f"(matched only: {found})")

    moved = _centroid_move(data)
    if moved is None:
        report.skip("drafted centroid moved by the operator", "no record", "no recorded edit")
    else:
        report.from_paper("drafted centroid moved by the operator",
                          r"moves the drafted centroid by ([\d.]+)~km", f"{moved:.1f}")


def _centroid_move(check: dict) -> float | None:
    """How far the operator moved the estate scenario's drafted centroid."""
    for record in check.get("scenarios", []):
        if record.get("id") != "estate-management":
            continue
        drafted = record.get("onboarded") or {}
        saved = record.get("after_operator_edits") or {}
        if None in (drafted.get("lat"), drafted.get("lon"), saved.get("lat"), saved.get("lon")):
            return None
        return haversine_km((drafted["lat"], drafted["lon"]), (saved["lat"], saved["lon"]))
    return None


def check_id_manifest(report: Report, result: dict) -> None:
    """The row counts again, from the tweet-ID manifest rather than the results."""
    print("\n[cross-check against eval/wnut2016_id_manifest.csv]")
    counts = _id_manifest_bucket_counts()
    adapter = _load_adapter()
    report.check("total rows (ID manifest)", str(result["counts"]["total"]),
                 str(sum(counts.values())))
    report.check("rows naming a catalogue place (ID manifest)",
                 str(result["post_level"]["per_bucket"]["intl"]["n_rows"]),
                 str(counts.get("intl", 0)))
    report.check("rows naming no catalogue place (ID manifest)",
                 str(result["post_level"]["per_bucket"]["hard-sem"]["n_rows"]),
                 str(counts.get("hard-sem", 0)))
    report.check("out-of-catalogue rows (ID manifest)", str(result["counts"]["ooc_rows"]),
                 str(counts.get("ooc", 0)))
    report.check("user-level scored rows (ID manifest)",
                 str(result["user_level"]["ensemble"]["n_evaluated"]),
                 str(counts.get("userhome", 0)))
    report.from_paper("in-catalogue mapping radius",
                      r"nearest catalogue coordinate within (\d+)~km",
                      f"{adapter.IN_CAT_KM:.0f}")
    report.from_paper("top-k (k)", r"\$k=(\d+)\$ by default",
                      str(result["manifest"]["k"]))

    print("\n[for reference: the adapter's per-bucket targets]")
    for bucket, cap in adapter.DEFAULT_TARGETS.items():
        realized = counts.get(bucket, 0)
        print(f"  {bucket:<10} realized={realized:<4} cap={cap:<4} "
              f"({'at cap' if realized == cap else 'below cap'})")


def print_not_checkable() -> None:
    print("\n[stated in the paper, not checkable from committed files]")
    for item in NOT_CHECKABLE:
        print(f"  - {item}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--paper-dir", default=PAPER_SECTIONS,
                    help="a copy of the paper's LaTeX sections (default: $GEOLENS_PAPER_DIR); "
                         "without it the paper-side checks are skipped")
    args = ap.parse_args(argv)

    results_dir = Path(args.results_dir)
    result = json.loads((results_dir / "wnut2016.json").read_text())
    banner = json.loads((results_dir / "wnut2016_banner.json").read_text())
    flag_rescore = json.loads((results_dir / "wnut2016_flag_rescore.json").read_text())

    paper_dir = Path(args.paper_dir) if args.paper_dir else None
    paper = paper_text(paper_dir) if paper_dir else None
    manifest = result["manifest"]
    print("source     : eval/results/wnut2016.json")
    if paper_dir is None:
        print("paper      : no --paper-dir given, checks skipped")
    else:
        print(f"paper      : {paper_dir if paper else f'{paper_dir} (not readable, checks skipped)'}")
    print(f"run        : {manifest['generated_at']}  GeoLens {manifest['version']}  "
          f"k={manifest['k']}  fusion={manifest['ensemble_method']}")
    print(f"catalogue  : {manifest['catalogue_size']} places, sha {manifest['catalogue_sha']}")
    print(f"engine mode: {', '.join(sorted(set(manifest['engine_modes'].values())))}")

    report = Report(paper)
    check_table(report, result, parse_paper_table(paper))
    check_prose(report, result)
    check_buckets(report, result)
    check_costs(report, result)
    check_run_variation(report, result, banner)
    check_flag(report, flag_rescore, result)
    check_gazetteer_artefacts(report, result)
    check_catalogue_geography(report)
    check_hosted_limits(report)
    check_example_set(report)
    check_scenario_checks(report)
    check_id_manifest(report, result)
    print_not_checkable()

    print()
    if report.skipped:
        print(f"{len(report.skipped)} check(s) skipped: {', '.join(report.skipped)}")
    if report.mismatches:
        print(f"{len(report.mismatches)} mismatch(es): {', '.join(report.mismatches)}")
        return 1
    print("every checked number reproduces from the committed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
