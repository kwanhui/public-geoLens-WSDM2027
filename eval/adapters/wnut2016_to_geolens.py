#!/usr/bin/env python3
"""Convert the WNUT-2016 geolocation validation set into a GeoLens eval CSV.

Every row is labelled from its GPS geotag mapped to the nearest catalogue
coordinate, the distant-supervision protocol standard in this literature, and
the five buckets (`intl`, `hard-sem`, `ooc`, `disagree`, `userhome`) are derived
by filtering real rows rather than by writing text. The script reads
`Validation Set.zip`, which comes from the shared task organisers under their
terms and is not in this repository, and writes CSVs that are git-ignored
because they carry rehydrated tweet text. `--verify-manifest` diffs the ids it
produced against the committed `eval/wnut2016_id_manifest.csv` per bucket and
exits non-zero on a mismatch. `eval/README.md` has the schema and the bucket
table.

Usage:
    python3 eval/adapters/wnut2016_to_geolens.py \
        --zip "/path/to/Validation Set.zip" \
        --out eval/wnut2016_test_set.csv \
        --sample-out eval/wnut2016_sample50.csv \
        --verify-manifest eval/wnut2016_id_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
import re
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._coords import CITY_COORDS
from geolens.engines.gazetteer import _count_matches
from geolens.geo import ACC_KM_THRESHOLD, haversine_km

REPO = Path(__file__).resolve().parents[2]

# The tag the paper cites for the committed results under eval/results/.
EVAL_TAG = "wnut2016-eval-3803853"

# What the committed run holds, per bucket. The adapter prints these beside
# what it just produced, so a regeneration that yields a different split is
# obvious rather than something a reader has to count for themselves.
COMMITTED_COUNTS = {
    "intl": 137,
    "hard-sem": 200,
    "ooc": 150,
    "disagree": 200,
    "userhome": 200,
}

# Commits after the tag that change what this adapter's engines return. A
# re-run at HEAD is a different run, and saying so here is cheaper than
# leaving a reproducer to find it by diffing numbers.
CHANGED_SINCE_TAG = """\
This tree is not at {tag}, which the paper cites for the committed results
under eval/results/. A later fix (0.14.1) corrected the gazetteer's
candidate list: at HEAD the list holds only the places the text named and an
abstention carries no place at all. The gazetteer's
top-1 place and score on a match are unchanged, so its own row is the same,
but the fused rows and any rank-based metric that reads the candidate list
will differ from the committed results. Re-run at the tag to reproduce
eval/results/wnut2016.json exactly.\
"""


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def tag_note() -> str | None:
    """A note about what changed since the evaluation tag, or None at the tag."""
    head = _git("rev-parse", "HEAD")
    tagged = _git("rev-list", "-n", "1", EVAL_TAG)
    if not head or not tagged:
        return None  # no checkout, or the tag is not fetched: say nothing
    if head == tagged:
        return None
    return CHANGED_SINCE_TAG.format(tag=EVAL_TAG)


def read_manifest_buckets(path: str) -> dict[str, set[str]]:
    """Row ids per bucket, from a committed or freshly written ID manifest."""
    buckets: dict[str, set[str]] = defaultdict(set)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            buckets[row["bucket"]].add(row["id"])
    return dict(buckets)


def verify_against_manifest(rows: list[dict], manifest_path: str) -> int:
    """Diff the ids just produced against a committed manifest, per bucket.

    Returns the number of buckets that differ, so the caller can exit
    non-zero. Prints what is missing and what is extra rather than only that
    the counts disagree: a regeneration that yields the right count from
    different rows is the failure worth catching.
    """
    expected = read_manifest_buckets(manifest_path)
    produced: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        produced[r["id"].rsplit("-", 1)[0]].add(r["id"])

    print(f"\nverifying against {manifest_path}")
    print("bucket           expected  produced  missing  extra")
    mismatches = 0
    for bucket in sorted(set(expected) | set(produced)):
        want, got = expected.get(bucket, set()), produced.get(bucket, set())
        missing, extra = want - got, got - want
        print(
            f"  {bucket:14s} {len(want):9d} {len(got):9d} {len(missing):8d} {len(extra):6d}"
        )
        if missing or extra:
            mismatches += 1
            for label, ids in (("missing", missing), ("extra", extra)):
                if ids:
                    sample = ", ".join(sorted(ids)[:5])
                    print(f"      {label}: {sample}{' ...' if len(ids) > 5 else ''}")
    if mismatches:
        print(f"\n{mismatches} bucket(s) differ from {manifest_path}.")
    else:
        print("\nevery bucket matches the committed manifest.")
    return mismatches

# Members inside "Validation Set.zip".
TWEET_GOLD = "Validation Set/validation.tweet.json"
TWEET_TEXT = "Validation Set/validation.tweet.json.TweetOut"
USER_GOLD = "Validation Set/validation.user.json"
USER_TEXT = "Validation Set/validation.user.json.TweetOut"

# A geotag whose nearest catalogue city is within this radius counts as
# in-catalogue; beyond it the tweet is treated as out-of-catalogue.
IN_CAT_KM = 50.0

# Default per-bucket targets (capped by what the data actually yields).
DEFAULT_TARGETS = {
    "intl": 250,
    "hard-sem": 200,
    "ooc": 150,
    "disagree": 200,
    "userhome": 200,
}

_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")

CSV_FIELDS = ["id", "post", "user_posts", "ground_truth_city", "bucket", "should_disagree", "source", "lang"]


def clean_text(text: str) -> str:
    """HTML-unescape, drop t.co URLs, collapse whitespace. Keeps @mentions and
    #hashtags (they carry location signal) and the original language."""
    if not text:
        return ""
    t = html.unescape(text)
    t = _URL_RE.sub("", t)
    t = t.replace("|", " ")  # the CSV uses '|' to separate user_posts
    return _WS_RE.sub(" ", t).strip()


def detect_lang(text: str) -> str:
    """Best-effort language tag. Returns 'und' if langdetect is unavailable."""
    try:
        from langdetect import detect  # type: ignore

        return detect(text) if text else "und"
    except Exception:
        return "und"


def nearest_city(lat: float, lon: float) -> tuple[str, float]:
    """Nearest catalogue city name and its great-circle distance (km)."""
    best_name, best_km = "", float("inf")
    for name, latlon in CITY_COORDS.items():
        d = haversine_km((lat, lon), latlon)
        if d < best_km:
            best_name, best_km = name, d
    return best_name, best_km


def has_toponym(text: str) -> bool:
    """True if any catalogue city name appears in the text (reuses the
    gazetteer's word-boundary / substring matcher)."""
    count, _ = _count_matches(text, DEFAULT_CITIES)
    return count > 0


def slug_to_name(slug: str) -> str:
    """`los angeles-ca037-us` -> `Los Angeles`; keeps out-of-catalogue cities
    human-readable as ground truth for the ooc bucket."""
    return slug.rsplit("-", 2)[0].strip().title()


def _read_jsonl(zf: zipfile.ZipFile, member: str):
    with zf.open(member) as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def _text_map(zf: zipfile.ZipFile, member: str) -> dict[str, str]:
    """tweet_id -> cleaned text."""
    out: dict[str, str] = {}
    for obj in _read_jsonl(zf, member):
        tid = obj.get("tweet_id")
        if tid:
            out[str(tid)] = clean_text(obj.get("text", ""))
    return out


def _flt(obj: dict, key: str):
    try:
        return float(obj[key])
    except (KeyError, TypeError, ValueError):
        return None


def build_tweet_rows(zf: zipfile.ZipFile) -> dict[str, list[dict]]:
    """Post-level rows grouped by bucket (intl / hard-sem / ooc)."""
    texts = _text_map(zf, TWEET_TEXT)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for obj in _read_jsonl(zf, TWEET_GOLD):
        tid = str(obj.get("tweet_id", ""))
        text = texts.get(tid, "")
        lat, lon = _flt(obj, "tweet_latitude"), _flt(obj, "tweet_longitude")
        if not text or lat is None or lon is None:
            continue
        name, km = nearest_city(lat, lon)
        if km <= IN_CAT_KM:
            bucket = "intl" if has_toponym(text) else "hard-sem"
            gt = name
        else:
            bucket = "ooc"
            gt = slug_to_name(obj.get("tweet_city", ""))
            if not gt or gt in CITY_COORDS:
                continue  # need a genuinely out-of-catalogue label
        buckets[bucket].append(
            {"post": text, "user_posts": "", "ground_truth_city": gt,
             "should_disagree": 0, "lang": detect_lang(text),
             "post_tweet_id": tid, "user_post_ids": ""}
        )
    return buckets


def build_user_rows(zf: zipfile.ZipFile) -> dict[str, list[dict]]:
    """User-level rows grouped by bucket (disagree / userhome).

    Each user's timeline is reconstructed from validation.user.json. A user is
    a `disagree` case when one tweet's geotag is >161 km from the home-city
    centroid and both map into the catalogue; otherwise it is a `userhome`
    negative control.
    """
    texts = _text_map(zf, USER_TEXT)
    by_user: dict[str, list[dict]] = defaultdict(list)
    for obj in _read_jsonl(zf, USER_GOLD):
        uid = obj.get("user_id")
        if uid:
            by_user[str(uid)].append(obj)

    buckets: dict[str, list[dict]] = defaultdict(list)
    for rows in by_user.values():
        home_lat, home_lon = _flt(rows[0], "user_city_latitude"), _flt(rows[0], "user_city_longitude")
        if home_lat is None or home_lon is None:
            continue
        home_name, home_km = nearest_city(home_lat, home_lon)
        if home_km > IN_CAT_KM:
            continue  # home must be in-catalogue

        timeline = []  # (tweet_id, text, tweet_lat, tweet_lon)
        for r in rows:
            tid = str(r.get("tweet_id", ""))
            text = texts.get(tid, "")
            tlat, tlon = _flt(r, "tweet_latitude"), _flt(r, "tweet_longitude")
            if text and tlat is not None and tlon is not None:
                timeline.append((tid, text, tlat, tlon))
        if len(timeline) < 2:
            continue

        # Find a divergent tweet: >161 km from home and in a different in-catalogue city.
        divergent = None
        for tid, text, tlat, tlon in timeline:
            if haversine_km((home_lat, home_lon), (tlat, tlon)) <= ACC_KM_THRESHOLD:
                continue
            tname, tkm = nearest_city(tlat, tlon)
            if tkm <= IN_CAT_KM and tname != home_name:
                divergent = (tid, text, tname)
                break

        if divergent:
            d_tid, post, post_city = divergent
            others = [(tid, text) for (tid, text, _, _) in timeline if tid != d_tid][:10]
            buckets["disagree"].append(
                {"post": post, "user_posts": "|".join(t for _, t in others),
                 "ground_truth_city": post_city, "should_disagree": 1,
                 "lang": detect_lang(post), "post_tweet_id": d_tid,
                 "user_post_ids": "|".join(i for i, _ in others)}
            )
        else:
            p_tid, post = timeline[0][0], timeline[0][1]
            others = [(tid, text) for (tid, text, _, _) in timeline[1:]][:10]
            buckets["userhome"].append(
                {"post": post, "user_posts": "|".join(t for _, t in others),
                 "ground_truth_city": home_name, "should_disagree": 0,
                 "lang": detect_lang(post), "post_tweet_id": p_tid,
                 "user_post_ids": "|".join(i for i, _ in others)}
            )
    return buckets


def assemble(zf: zipfile.ZipFile, targets: dict[str, int], seed: int) -> list[dict]:
    rng = random.Random(seed)
    pools = build_tweet_rows(zf)
    pools.update(build_user_rows(zf))
    rows: list[dict] = []
    print("bucket            available  taken  committed run")
    for bucket in ("intl", "hard-sem", "ooc", "disagree", "userhome"):
        pool = pools.get(bucket, [])
        rng.shuffle(pool)
        take = pool[: targets.get(bucket, 0)]
        expected = COMMITTED_COUNTS[bucket]
        flag = "" if len(take) == expected else "   <- differs"
        print(f"  {bucket:14s}  {len(pool):8d}  {len(take):5d}  {expected:13d}{flag}")
        for i, r in enumerate(take):
            r = dict(r)
            r["id"] = f"{bucket}-{i:04d}"
            r["source"] = "wnut2016"
            rows.append(r)
    return rows


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


# Columns of the shareable reproducibility manifest: tweet IDs + derived labels
# only (no tweet text), so anyone with a WNUT copy can reconstruct the exact
# text CSV. Tweet IDs are redistributable under Twitter/X terms; text is not.
MANIFEST_FIELDS = ["id", "bucket", "ground_truth_city", "should_disagree", "lang", "post_tweet_id", "user_post_ids"]


def write_id_manifest(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for r in rows:
            out = {k: r.get(k, "") for k in MANIFEST_FIELDS}
            out["bucket"] = r["id"].rsplit("-", 1)[0]
            w.writerow(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="WNUT-2016 -> GeoLens eval CSV adapter")
    p.add_argument("--zip", required=True, help="Path to 'Validation Set.zip'")
    p.add_argument("--out", default="eval/wnut2016_test_set.csv")
    p.add_argument("--sample-out", default="eval/wnut2016_sample50.csv",
                   help="A <=50-row stratified subset for the hosted Space (its 50-row cap).")
    p.add_argument(
        "--id-manifest",
        default="eval/wnut2016_id_manifest_regenerated.csv",
        help=(
            "Where to write the tweet-ID + label manifest (no tweet text). It "
            "deliberately does not default to eval/wnut2016_id_manifest.csv: "
            "that file is the committed record a reproducer diffs against, and "
            "overwriting it destroys the comparison. Use --verify-manifest to "
            "make the comparison."
        ),
    )
    p.add_argument(
        "--verify-manifest",
        default=None,
        metavar="PATH",
        help=(
            "Diff the ids just produced against a committed ID manifest, per "
            "bucket, and exit non-zero on any mismatch. Pass "
            "eval/wnut2016_id_manifest.csv to check a regeneration against the "
            "committed run."
        ),
    )
    p.add_argument("--seed", type=int, default=13)
    args = p.parse_args(argv)

    note = tag_note()
    if note:
        print(note + "\n")

    with zipfile.ZipFile(args.zip) as zf:
        rows = assemble(zf, DEFAULT_TARGETS, args.seed)
    write_csv(args.out, rows)
    print(f"\nwrote {len(rows)} rows -> {args.out}  (the committed run has 887)")
    write_id_manifest(args.id_manifest, rows)
    print(f"wrote tweet-ID manifest -> {args.id_manifest}")

    # Stratified <=50-row sample: round-robin across buckets so every bucket is
    # represented within the hosted Space's 50-row cap.
    rng = random.Random(args.seed)
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_bucket[r["id"].rsplit("-", 1)[0]].append(r)
    for v in by_bucket.values():
        rng.shuffle(v)
    sample: list[dict] = []
    while len(sample) < 50 and any(by_bucket.values()):
        for v in by_bucket.values():
            if v and len(sample) < 50:
                sample.append(v.pop())
    write_csv(args.sample_out, sample)
    print(f"wrote {len(sample)} rows -> {args.sample_out}")

    if args.verify_manifest and verify_against_manifest(rows, args.verify_manifest):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
