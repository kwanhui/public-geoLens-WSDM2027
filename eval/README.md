# GeoLens evaluation

This directory holds the bundled example test set, the WNUT-2016 adapter and
the committed results the paper cites, together with the CSV schema and the
protocol for running bulk inference and bulk evaluation against a GeoLens
instance.

## CSV format

| column | type | required | notes |
|---|---|---|---|
| `id` | string | yes | a stable identifier, used in result rows |
| `post` | string | one-of-required | a single post for post-level engines |
| `user_posts` | string | one-of-required | pipe-separated (`\|`) recent posts for user-level engines |
| `user_handle` | string | optional | echoed back and otherwise ignored, as no engine reads it, and a row carrying only a handle is refused |
| `ground_truth_city` | string | optional for `/batch_predict`; required for `/eval` | an exact catalogue place name, or a place outside it (reported as `OOC`). The post-level engines are scored against this |
| `ground_truth_user_city` | string | optional | the home place of the account behind `user_posts`. Where it is present the user-level engines and fusion are scored against it, and where it is absent every engine is scored against `ground_truth_city` |
| `bucket` / `tag` | string | optional | difficulty label for the per-difficulty breakdown; if absent it is derived from the `id` prefix (`hard-sem-3` → `hard-sem`) |
| `should_disagree` | bool (`1`/`0`) | optional | gold label for the verification flag, `1` where the row's post-level and user-level locations conflict. `/eval` then reports the flag's precision and recall against these labels |

Each row needs a `post`, a `user_posts` timeline, or both. A row with no
timeline does not run the user-level engines, so it produces neither a
user-level fused prediction nor a verification flag. A row with no `post`
does not run the post-level engines, for the same reason, which is what
prevents the flag from comparing one body of text with itself.

An uploaded file has to be UTF-8, with or without the byte-order mark Excel
writes, and anything else is a 400. It needs an `id` column and a non-empty
id on every row, unique within the request; a missing header, a blank id or
a duplicate is a 422 naming the header, the line or the duplicates, and an
id is never replaced by a row number. `/eval` also needs a
`ground_truth_city` or `ground_truth_user_city` column with at least one row
carrying a value. CRLF line endings and newlines inside quoted cells parse
as one row per record, and ground-truth values are trimmed and normalised
before matching, so `Bedok ` is not scored as out of catalogue.

`on_row_error` takes `fail`, the default, under which one unusable row
refuses the whole request with a 422 naming it, or `skip`, under which that
row comes back with `status: "error"` and its own message,
`summary.error_rows` counts it, and the rest run. The size caps refuse the
whole request under either policy.

## Install and run

See the repository README for `make demo`. The examples below use the hosted
instance.

```
curl -s -X POST https://kwanhui-geo-lens.hf.space/eval \
  -H 'Content-Type: application/json' \
  -d '{"inputs":[{"id":"1","post":"Queue at the Bedok hawker centre again","ground_truth_city":"Bedok"}]}'

curl -s -X POST https://kwanhui-geo-lens.hf.space/eval_csv \
  -F 'file=@eval/example_test_set.csv'
```

`/batch_predict` takes the same shape without the ground-truth fields. In
the interface, the Bulk Evaluation tab takes the CSV and shows the
per-engine summary, the per-row predictions and the two downloads.

To score a place the catalogue does not hold, onboard it first. The optional
`region` hint goes into the drafting prompt, and the drafted centroid is
checked against the country it names.

```
curl -s -X POST https://kwanhui-geo-lens.hf.space/onboard \
  -H 'Content-Type: application/json' \
  -d '{"city":"Bidadari Estate","region":"Singapore"}'
```

The response carries `catalogue_status` (`added` or `already_present`) and a
`warnings` list. `DELETE /onboard` takes the place back out, except that a
built-in place is never dropped, so a profile drafted or edited for one is
an overlay and `DELETE` restores the built-in profile
(`catalogue_status: "built_in_restored"`).

## Choosing the engines

`/geolocate`, `/batch_predict` and `/eval` take an optional `engines` list,
and both CSV forms take an `engines` form field of comma-separated names.
Omitting it runs every engine, and naming one outside the registry returns
422. An engine left out is reported with `skipped: true` and
`reason: "not selected"`, and the selection is recorded in the manifest. The
interface offers two presets, "All engines" and "Local engines only". The
second runs the gazetteer at both levels and the three frozen encoders, so no
text leaves this server. Picking a preset is the caller's decision;
the server never substitutes one engine for another.

`GET /instance` lists the registry with each engine's label, tag, tooltip,
granularity and family, names the engines that send no text to a third
party in `local_engines`, and reports what the deployment enforces. It also carries
two sets of measured results read from the committed WNUT-2016 run:
`flag_reference`, what the verification flag measured, and
`engine_reference`, each engine's Acc@1 with its 95% Wilson interval and the
rows behind it, split for a post-level engine into the 137 rows whose text
named a catalogue place and the 200 whose text named none.

## The error contract

Every error from every endpoint has one shape:

```json
{
  "error": {"code": "missing_id_header", "message": "...", "field": "file", "details": []},
  "detail": "..."
}
```

`error.code` is the stable machine-readable name and `detail` repeats the
message as a plain string, so a client can read every refusal through one
branch. The OpenAPI document declares every status code each operation can
return, and a field the request models do not know is a 422 rather than a
setting silently ignored.

A call that returns without a usable answer is not an error. A prediction
carries `failed: true` for a call that raised or could not be read, and
`outcome: "no_catalogue_place"` when the model answered and named nothing in
the closed catalogue. Neither enters the fused prediction, the consensus or the flag, and only the first of the two is
recorded as a failure.

## Metrics reported (`/eval`, `/eval_csv`)

Per engine and per fusion level:

- Acc@1 and Acc@5, each with a 95% Wilson score interval. Below ten scored
  rows the interface prints counts instead of a rate.
- Mean rank of the ground-truth place in the top-k, and mean rank (found)
  over the rows where the truth was in the list. The plain mean averages in
  a 1,000,000 sentinel for an abstention, so read `mean_rank_found` and
  `n_rank_found`.
- Median and mean great-circle error in km and Acc@161km, over the rows the
  engine answered on, with an abstention counted separately.
- Median latency and total cost in USD per engine.
- A per-difficulty breakdown of Acc@1 by bucket, and a
  differs-from-best-single rate for each fused prediction.

The summary states the closed-set candidate count `N`. Fusion is selectable
per request through `ensemble_method`: `weighted`, the sum of each engine's
top-k scores, or `rrf`, reciprocal rank fusion.

Every `cost_usd` is a token count multiplied by a list price from
`geolens/pricing.py`. It is not read from a bill, and a model the table does
not list reports no cost rather than another model's rates.
`cost_is_estimated` on each prediction says which it is, and the manifest's
`price_table` records the rates and the date they were checked. One rate has
been corrected since the evaluation run; see the errata.

### The run manifest and the two downloads

Every response carries a run manifest. It records the build and commit that
produced the result, the run settings (`k`, `ensemble_method`,
`flag_radius_km`, `selected_engines`), the model identifier and the call
outcome for each engine together with the `sampling_parameters` read off its
adapter, the input digest `input_sha256` and row count, the candidate
catalogue and its hash, held steady for the whole run, the gazetteer's
per-level abstention and match counts, the distance method and the price
table. The single-query export adds the submitted text, its digest and a
timestamped file name.

A response also carries `place_id`, `place_coordinates`, `error_km`,
`within_161km`, `places` and a per-engine `mode`, so that neither download
has to join on a place name, and an engine's `error_km` is measured against
the truth for its own level. The Bulk Evaluation tab writes a per-row CSV,
one line per uploaded row with seven columns per engine, and an RFC 7946
GeoJSON with one feature per row, level and engine. Every field of all
three is described by the OpenAPI document at `/docs`.

## Coordinate provenance

`GET /catalogue` gives every built-in place a `feature_type` (country, city,
town, estate, street) and a `centroid_source`, because an error of 5 km means
something different against a country's centroid than against a street's.
The 28 places added for the WNUT-2016 evaluation carry
`wnut2016-gold-city-centre`, as their points are the shared task's gold city
centres. The 22 seed places carry `seed-approximate`, as their points were
entered by hand in 0.7.0 with no source recorded, and
seven of them are not cities: Singapore is a country, five are Singapore
planning areas, and `Tengah Plantation Crescent` is a street. An onboarded
place carries null for both.

Every coordinate is written to four decimal places, and distances between
them are haversine on a sphere of radius 6371.0088 km. Against a WGS-84
ellipsoidal distance over this catalogue, the difference never changes
whether the verification flag is raised at the 161 km default.

## The verification flag's radius

`flag_radius_km` is the great-circle separation at which the flag is raised
when the post-level and user-level consensus places differ. Every query
endpoint takes it, and `python3 -m geolens.cli eval` takes
`--flag-radius-km`. It defaults to 161 km (100 miles), the value every
reported result was produced under, is accepted in the range 1 to
20,015 km, and the value used is recorded in the run manifest.

The flag compares the **consensus** place of each level, which is each
engine's top prediction weighted by its confidence. That is a different
quantity from the **fused prediction**, which sums each engine's top-k scores
or applies reciprocal rank fusion, so the two can name different places for
the same level. Both pairs are in every response and in both exports. The
flag does not depend on `ensemble_method`.

### Rescoring the flag from the committed results

```
python3 scripts/rescore_flag.py
```

It prints the confusion counts, precision and recall with 95% Wilson
intervals for the 400 user-timeline rows and for the wider 737 catalogued
rows, checks each row count against `wnut2016_id_manifest.csv`, and writes
`results/wnut2016_flag_rescore.json`. The 400-row set is the one the paper
reports, because a post-only row has no timeline, so the user-level engines
fall back to the post text and the row can fire the flag with nothing to
verify against.

## The bundled example set

`example_test_set.csv` has 50 rows in twelve buckets. It is authored by hand
and labelled by intent, so its accuracies run far above the WNUT-2016 run
the paper reports, and the bulk view says so on screen.

| bucket | n | what it stresses |
|---|---|---|
| `sg-explicit-*` | 6 | a named Singapore HDB estate |
| `sg-implicit-*` | 4 | Singapore implied by foods, slang or landmarks |
| `osint-*` | 4 | post location differs from user activity |
| `intl-*` | 6 | international places in the catalogue |
| `crisis-*` | 4 | mixed Indonesian and English flood posts |
| `ooc-*` | 2 | ground truth outside the catalogue |
| `hard-sem-*` | 7 | no place names, only cultural cues |
| `disagree-*` | 4 | post and timeline point at different cities |
| `userhome-*` | 4 | timeline consistent with the post, `should_disagree=0` |
| `multilang-*` | 4 | Vietnamese, Indonesian and Thai posts |
| `sarcasm-*` | 2 | sarcasm and negation |
| `ambig-*` | 3 | ambiguous between Asian metros |

Twelve rows carry a timeline and a `ground_truth_user_city`, that is, the
eight labelled disagreements and the four home-consistent ones, and those
twelve are the rows the flag can fire on. The set is a case study rather
than a benchmark, as it is curated to stress the workbench across difficulty
levels and its places are weighted towards Singapore and Asia. Comparing
GeoLens with other systems requires a larger benchmark of your own, run
through `/batch_predict` or `/eval`.

## WNUT-2016 adapter

### Where the data comes from

The validation set is published by the WNUT-2016 Twitter geolocation shared
task, described in Han, Rahimi, Derczynski and Baldwin, "Twitter Geolocation
Prediction Shared Task of the 2016 Workshop on Noisy User-generated Text"
(W-NUT 2016). Obtain `Validation Set.zip` from the shared task organisers
under their terms and point `--zip` at your own copy. This repository holds
no tweet text, because Twitter/X terms permit redistributing tweet IDs only,
which is what `eval/wnut2016_id_manifest.csv` carries.

### What the adapter does

`adapters/wnut2016_to_geolens.py` converts the validation set into the CSV
schema above. Every row is labelled from its GPS geotag mapped to a GeoNames
metropolitan centre, which is the distant-supervision protocol standard in
this literature (Eisenstein et al. 2010; Han et al. 2014). Buckets are
derived by filtering real rows, never by writing text:

| bucket | how it is selected | label source |
|---|---|---|
| `intl` | geotag within 50 km of a catalogue city, text names a catalogue city | geotag |
| `hard-sem` | geotag in-catalogue, text names no catalogue city | geotag |
| `ooc` | geotag far from every catalogue city; the real city is kept as ground truth | geotag |
| `disagree` | a user whose home city is in-catalogue but who has one tweet geotagged to a different in-catalogue city more than 161 km away; that tweet is `post`, the rest is `user_posts`, `should_disagree=1` | geotag |
| `userhome` | a home-consistent user; `should_disagree=0` | geotag |

WNUT-2016 is English-framed, so the multilang, crisis, sarcasm and ambig
buckets stay in the authored set. The catalogue is expanded to 50 places,
the most frequent WNUT metros with centroids taken from the gold city
centres. The committed run holds 887 rows: 137 `intl`, 200 `hard-sem`, 150
`ooc`, 200 `disagree`, 200 `userhome`, and the adapter prints those counts
beside what it produced. `intl` is 137 rather than the configured 250
because the pool of in-catalogue tweets naming a catalogue place is smaller
than the cap.

### Reproducing the run

Generate the CSVs, which are git-ignored because they carry tweet text:

```
python3 eval/adapters/wnut2016_to_geolens.py \
    --zip "/path/to/Validation Set.zip" \
    --out eval/wnut2016_test_set.csv \
    --sample-out eval/wnut2016_sample50.csv \
    --verify-manifest eval/wnut2016_id_manifest.csv
```

`--verify-manifest` diffs the ids just produced against the committed
manifest per bucket, prints what is missing and what is extra, and exits
non-zero on any mismatch. `--id-manifest` writes beside the committed file
rather than over it.

Then score. The LLM engines need `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`
and the encoders download their checkpoints on first run:

```
python3 -m geolens.cli eval eval/wnut2016_test_set.csv \
    --out eval/results/wnut2016.json
```

`--stub` validates the pipeline offline in placeholder mode, in which case
the reported numbers carry no information.

### How the paper's table was produced

The table was produced by `adapters/run_wnut_eval.py` rather than by
`/eval`. That script runs every engine once over the 887 rows and then
scores each engine on the rows where its task is well defined. Post-level
engines are scored on the `intl`, `hard-sem` and `ooc` buckets against the
post's city, and user-level engines on the `userhome` bucket against the
account's home city. The `disagree` rows are scored against neither, because
they carry two truths, and they contribute to the verification flag alone.
The flag is reported over the rows that carry a timeline, that is, `disagree`
together with `userhome`, and those counts are in
`results/wnut2016_banner.json`.

`/eval` applies the same per-level rule once each row carries a truth for
each level. With one truth column a `disagree` row would score the user-level
engines against the city the post was written in, and
`ground_truth_user_city` is what separates the two:

```
id,post,user_posts,ground_truth_city,ground_truth_user_city,should_disagree
disagree-1,Fire at Marina Bay Sands,Ramen in Tokyo|Tokyo again,Singapore,Tokyo,1
userhome-1,Queue at the Bedok hawker centre,Bedok reservoir run|Bedok again,Bedok,Bedok,0
```

Two differences from the adapter remain. First, `/eval` scores every engine
on every row it is given, so restricting an engine to its own buckets means
uploading one file per stratum. Second, it reports no paired McNemar test.

### Checking the paper's table against the results

```
python3 scripts/recompute_table.py --paper-dir path/to/paper/sections
```

The script runs offline. It recomputes every figure in the paper's results
table and evaluation prose from the three files under `results/`, and prints
each one beside the value that the paper prints, read from a copy of the
paper's LaTeX sections. It exits non-zero on a mismatch and skips with a note
any figure the paper no longer prints. Without `--paper-dir` it skips the
comparisons with the paper.

## Limits and the shared catalogue

Per-request size caps apply everywhere, including a local run.
`MAX_BATCH_ROWS` (50) is rows per bulk run, `MAX_BATCH_BYTES` (200,000) is
bytes per request body, and a bulk run carries at most 200,000 characters of
text over all its rows. Over any of the three the endpoint returns 413.

Everything else is a deployment setting that the package leaves off, so a
local run is not rate-limited, and `demo/deployment.md` lists each setting
with its package default and its hosted value. Two of them change what a
run reports rather than only how often it may be made. While the
estimated-spend ceiling is reached, which is one budget for the whole
process rather than per address, the engines that call a paid model come
back skipped and the local engines answer as usual. One hosted instance
also serves one catalogue to everyone, so a place any visitor onboards
becomes a candidate for every visitor after them until it expires on its
time to live. `GET /instance`, `GET /onboard/status` and every run manifest
report the state a request ran under.

A place is a neighbourhood or a town, and an instance can be set to refuse
a name that reads as a building or a street address. Every place name,
alias and landmark has to read as a name before it reaches either prompt or
the gazetteer, and every profile field is length-bounded, with anything
outside the bounds returning a 422 that names the field.
`onboarding/validation.py` states each rule, its numeric bound and the
message it raises.

## Errata: the committed results and the code at `main`

The committed results under `results/` were produced at tag
**`wnut2016-eval-3803853`**, which marks the 0.8.0 release (the suffix is a
commit id from the development repository). The tag was cut after both runs
finished, so it records the tree the runs were made from rather than a tree
they were launched at. To reproduce `results/wnut2016.json`
exactly, check out the tag and run from there. Four differences between that
tree and `main` affect the numbers, and the adapter prints this note itself
when the tree is not at the tag.

First, the gazetteer's candidate list was corrected after the tag. A fix
released in 0.14.1 stopped padding the list up to `k` with places the text
did not name, each of which scored `1/total`, and made an abstention carry no
place and an empty list. At the tag an abstaining gazetteer carried the first
catalogue place, Singapore, with a small vote. Its top-1 place and score on
a match are unchanged, but its Acc@1 row can still hold chance hits: one of
its post-level Acc@1 hits and up to three of its 33 user-level hits can be
an abstention that landed on Singapore. Four of its 130 post-level hits
within 161 km came the same way, so its post-level Acc@161 km is .37 (126 of
337) without them. The per-level consensus behind the verification flag read
that vote too, so the flag's counts at the tag differ as well, not only the
fusion rows.

Second, a failed LLM call was scored as a prediction. At the tag an adapter
whose call raised, or whose reply could not be parsed, returned a placeholder
place, and that place entered the metrics as the engine's answer. The run
recorded no count of such rows. At `main` a failed call carries no place, is
excluded from the fusion, the consensus and the flag, and `engine_modes` and
`engine_call_counts` record what each call did.

Third, the Claude adapter's sampling changed. Both runs sent `temperature=0`.
The anthropic 1.x SDK, adopted on 2026-09-02, removed the sampling parameters
from `Messages.create` and current models reject them API-side, so the
adapter sends none and the classifier runs at the model's default sampling.

Fourth, the cost column was computed at the wrong Claude rate. Both runs
priced `claude-haiku-4-5-20251001` at USD 0.80 per million input tokens and
4.00 per million output, which is the Claude Haiku 3.5 price; the list price
for 4.5 is 1.00 and 5.00, so every Claude cost in `results/` is 0.8 of the
correct figure. The result files are not edited, because they record what the
run computed, and `scripts/recompute_table.py` multiplies the two Claude
engines' totals by 1.25 and prints the recorded value, the factor and the
reason beside the corrected figure. Corrected, the cost per 1,000 rows is
0.55 at the post level and 0.81 at the user level, and the Claude Haiku 4.5
to GPT-4o-mini ratios are 8.2 and 9.2. No accuracy, latency or row count is
affected.

These differences are disclosed in the paper.
