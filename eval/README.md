# GeoLens evaluation

This directory holds the **example test set** that ships with GeoLens
plus instructions for running bulk inference / bulk evaluation against the
workbench.

## CSV format

| column | type | required | notes |
|---|---|---|---|
| `id` | string | yes | a stable identifier; used in result rows |
| `post` | string | one-of-required | a single post for post-level engines |
| `user_posts` | string | one-of-required | pipe-separated (`\|`) recent posts for user-level engines |
| `user_handle` | string | optional | a Twitter/X handle if relevant |
| `ground_truth_city` | string | optional for `/batch_predict`; required for `/eval` | exact city name from the catalogue, OR a city outside the catalogue (will be reported as `OOC`) |
| `bucket` / `tag` | string | optional | difficulty/category label for the per-difficulty breakdown; if absent it is derived from the `id` prefix (e.g. `hard-sem-3` → `hard-sem`) |
| `should_disagree` | bool (`1`/`0`) | optional | gold label for the cross-task disagreement banner: `1` if the row's post-level and user-level locations genuinely conflict, `0` otherwise. When present, `/eval` reports the banner's precision and recall against these labels |

Each row needs at least one of `post`, `user_posts`, or `user_handle`.

## Metrics reported (`/eval`, `/eval_csv`)

Per engine and per ensemble bucket:

- **Acc@1, Acc@5**. Each Acc@1 carries a 95% Wilson score interval, so small-set gaps are not over-read.
- **Mean rank** of the ground-truth city in the top-k.
- **Median / mean great-circle error (km)** and **Acc@161km** (within 100 miles), the usual distance metrics in geolocation work (Eisenstein et al. 2010; Han et al. 2014), so a near-miss is not scored like a far-miss.
- **Median latency** and **total cost (USD)** per engine.
- A **per-difficulty breakdown** of Acc@1 by bucket.
- A **differs-from-best-single** rate for each ensemble.

The summary states the closed-set candidate count `N` (so Acc@1 is interpretable), and every response carries a **run manifest** (tool version, model ids, catalogue size + hash, `k`, fusion method, timestamp) for reproducibility. Fusion is selectable per request via `ensemble_method` (`weighted` sum or `rrf`, Reciprocal Rank Fusion). The per-row CSV export includes each engine's top-1 prediction for error analysis.

## Example: `example_test_set.csv`

The bundled example has 50 rows split into eleven difficulty buckets:

| bucket | n | what it stresses |
|---|---|---|
| `sg-explicit-*` | 8 | Posts that explicitly name a Singapore HDB estate; gazetteer should win, encoders should match |
| `sg-implicit-*` | 6 | Posts that imply Singapore via foods / slang / landmarks; favours encoder + LLM methods |
| `osint-*` | 4 | Post location ≠ user activity location; tests cross-task verification (the disagreement banner) |
| `intl-*` | 6 | International cities in the catalogue (KL, Tokyo, Manila, etc.) |
| `crisis-*` | 4 | Mixed Indonesian/English flood posts; tests RetrieveZero's MoR-enriched cities |
| `ooc-*` | 2 | Ground-truth city not in catalogue (Phoenix, Munich); should be reported as `OOC` |
| `hard-sem-*` | 7 | No place names at all, only local cultural cues (NS, COE, MTR strike). Gazetteer should fail; encoders / LLMs should win |
| `disagree-*` | 4 | Stronger version of OSINT: post points to one Asian city, user history points firmly to another. Tests ensemble + cross-task verification together |
| `multilang-*` | 4 | Vietnamese / Indonesian / Thai language posts. Tests LLM and encoder breadth beyond English |
| `sarcasm-*` | 2 | Sarcasm / negation. LLMs should handle; gazetteer mentions can be misleading |
| `ambig-*` | 3 | Genuinely ambiguous (any Asian metro). Tests where the ensemble has room to win because no single engine is confident |

## Running the eval

### Web UI (drag-and-drop)

1. Open <https://kwanhui-geo-lens.hf.space>
2. Switch to the **Batch eval** tab
3. Drop the CSV in the upload zone, click **Run eval**
4. Review the per-engine summary table + per-row predictions
5. Download the results as CSV or copy the LaTeX table

### HTTP API (curl / scripts)

JSON body:
```
curl -s -X POST https://kwanhui-geo-lens.hf.space/eval \
  -H 'Content-Type: application/json' \
  -d '{"inputs":[{"id":"1","post":"Tengah hawker queue","ground_truth_city":"Tengah Plantation Crescent"}]}'
```

CSV upload:
```
curl -s -X POST https://kwanhui-geo-lens.hf.space/eval \
  -F 'file=@eval/example_test_set.csv'
```

For `/batch_predict` (no metrics), use the same shape minus the
`ground_truth_city` field.

## Limits

The live HF Space caps:
- 50 rows per batch (set `MAX_BATCH_ROWS` env var)
- 200 KB CSV file size (set `MAX_BATCH_BYTES`)
- 5 batches per IP per hour (set `MAX_BATCHES_PER_HOUR`)

Running locally with the same package removes these limits; see the repo
README for `make demo` and `python -m geolens.app`.

## Scope

This is a **case-study evaluation, not a benchmark**. The example set was
written by hand to exercise the workbench across difficulty levels and
granularities, and its cities lean heavily towards Singapore and the rest of
Asia. The LLM engines won nearly every row of the first 30, so the rows added
on 2026-05-10 (`hard-sem-*`, `disagree-*`, `multilang-*`, `sarcasm-*`,
`ambig-*`) cover what those rows did not: posts with no place names, strong
post-vs-user inconsistency, non-English text, sarcasm, and posts that could
come from several Asian cities.

To compare GeoLens with other geolocation systems, use a larger benchmark
(GeoText, TwitterUS, TwiU/FliU subsets) and run it through `/batch_predict` or
`/eval`. The scripts that convert external benchmarks into our CSV schema are
in `adapters/`.

## WNUT-2016 adapter (geotag-labelled evaluation)

`adapters/wnut2016_to_geolens.py` converts the WNUT-2016 Twitter geolocation
shared-task validation set into the CSV schema above. Unlike
`example_test_set.csv` (hand-authored, labelled by intent), every WNUT row is
labelled from its GPS geotag mapped to a GeoNames metropolitan centre, the
standard distant-supervision protocol in the geolocation literature (Eisenstein
et al. 2010; Han et al. 2014), so the labels do not depend on our judgement. At
~900 rows it also tightens the Wilson intervals that make the 50-row encoder
ranking inseparable.

Buckets are derived by *filtering* real rows, never by writing text:

| bucket | how it is selected | label source |
|---|---|---|
| `intl` | geotag within 50 km of a catalogue city, text names a catalogue city | geotag |
| `hard-sem` | geotag in-catalogue, text names no catalogue city (placeable only by geotag) | geotag |
| `ooc` | geotag far from every catalogue city; the real city is kept as ground truth | geotag |
| `disagree` | a user whose home city is in-catalogue but who has one tweet geotagged to a different in-catalogue city >161 km away; that tweet is `post`, the rest is `user_posts`, `should_disagree=1` | geotag |
| `userhome` | a home-consistent user; `should_disagree=0` (negative control + user-level case) | geotag |

WNUT-2016 is English-framed, so the multilang / crisis / sarcasm / ambig
buckets stay in the authored set. The candidate catalogue is expanded to ~50
cities (the most frequent WNUT metros, with centroids taken from the gold city
centres) so more of its rows map in-catalogue.

Generate the CSVs (they are **git-ignored**: they contain rehydrated tweet
text, and Twitter/X terms permit redistributing tweet IDs only, so regenerate
from your own licensed copy):

```
python3 eval/adapters/wnut2016_to_geolens.py \
    --zip "/path/to/Validation Set.zip" \
    --out eval/wnut2016_test_set.csv \
    --sample-out eval/wnut2016_sample50.csv
```

Then score (the LLM engines need `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`; the
encoder engines download their checkpoints on first run, so a real run is best
done locally or on the hosted Space, not in a sandbox):

```
python3 -m geolens.cli eval eval/wnut2016_test_set.csv \
    --out eval/results/wnut2016.json
```

The run manifest in the output records each engine's real/stub mode, so a stub
fallback is never mistaken for a real number. Pass `--stub` to validate the
pipeline offline (the numbers are then meaningless and must not be reported).

For the paper table, use `adapters/run_wnut_eval.py` instead of the plain
`eval` command: it runs every engine once, then scores each on the rows where
its task is well-defined (post-level engines on `intl`/`hard-sem`/`ooc` against
the post city; user-level engines on `userhome` against the home city; the
`disagree` rows feed only the cross-task banner, since they carry two truths),
and adds paired McNemar tests. It writes `results/wnut2016.json`. The banner is
reported over the rows that actually carry a user timeline (`disagree` +
`userhome`); run the scorer on a CSV filtered to those buckets for that number.
