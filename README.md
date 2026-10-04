---
title: GeoLens
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Comparing zero-shot social media geolocation engines
---

# GeoLens

Live instance: <https://kwanhui-geo-lens.hf.space> (Hugging Face Spaces).
Video: <https://youtu.be/ArKLLrxWXCk>.
Source: <https://github.com/kwanhui/public-geoLens-WSDM2027>.

GeoLens runs six geolocation engines on one input, three of them at both the
post and the user level, so nine engine instances in all. They sit behind one
page and one API, over a closed catalogue of 50 candidate places. GeoLens
reports each engine's answer with its latency and cost, fuses the engines at
each level, and raises a verification flag when the post-level and user-level
consensus places are far apart. An operator can add a place to the catalogue
at run time from an LLM-drafted form, and a labelled CSV is scored with Wilson
intervals, distance metrics and a run manifest.

The caller chooses which engines run. The page offers two presets, all
engines and the local engines only, and the API takes an `engines` list. With
the local preset, the gazetteer and the three frozen encoders run on this
server and no text is sent to a third party.

This repository holds the runnable system. The companion paper is listed at
the end, under Citation.

## Install and run

```bash
git clone https://github.com/kwanhui/public-geoLens-WSDM2027.git
cd public-geoLens-WSDM2027

python3 -m venv venv
source venv/bin/activate
make install
make demo
```

Open <http://localhost:7860>. The page opens on Post Geolocation, where you
paste a post and press Geolocate. User Geolocation takes an account's recent
posts instead, and Post vs. User Verification takes both and compares them.

`OPENAI_API_KEY` drives the GPT-4o-mini classifier and the onboarding
drafter; `ANTHROPIC_API_KEY` drives the Claude Haiku classifier. The encoder
engines need `pip install -e ".[real]"` (torch and transformers) and download
their checkpoints on first use. Without any of that the instance runs in
placeholder mode, in which the gazetteer still answers for real and every
other engine returns a clearly marked placeholder. Put the keys in a local
`.env` file and never commit it.

```bash
echo "OPENAI_API_KEY=sk-..." > .env
export $(cat .env | xargs)
```

From the command line:

```bash
python3 -m geolens.cli geolocate --post "Just landed at Changi, ready for kaya toast"
python3 -m geolens.cli onboard --city "Bidadari Estate" --region "Singapore"
python3 -m geolens.cli eval eval/example_test_set.csv --out /tmp/summary.json
```

`geolocate` runs the same roster the server does, from
`geolens.engines.registry`.

## Names on the API and in the paper

The API's field names are older than the paper's vocabulary, and both are
stable. The table below reads across the two.

| API name | The paper's term |
| --- | --- |
| `triangulation`, `consensus_city` | the consensus |
| `ensembles` | the fused prediction |
| `mode: "stub"` | placeholder |
| `disagreement_flag` | the verification flag |

## The engines

| Engine | Level | What runs here |
| --- | --- | --- |
| ContrastGeo | post | Frozen SimCSE BERT-large. Embeds the place name, scores the post against it by cosine similarity. |
| FewUser | user | Frozen SimCSE RoBERTa-large. Embeds `a social media user from <name>`. |
| RetrieveZero | user | Frozen E5-large. Embeds a passage built from the place's drafted profile where there is one, which there is only for an onboarded place, while for a built-in place it embeds the bare name. Onboarding is what changes its answer. |
| Gazetteer | post and user | Counts the place name, its aliases and, for an onboarded place, its landmarks in the text. Abstains when it finds none of them. |
| GPT-4o-mini | post and user | Prompted classifier over the catalogue. |
| Claude Haiku 4.5 | post and user | Prompted classifier over the catalogue, same prompt. |

ContrastGeo, FewUser and RetrieveZero carry the names of published methods.
As hosted they are frozen-encoder versions of them. Nothing in the interface
or the API takes support examples; each one embeds a string built from the
place name (for RetrieveZero, from the drafted profile) with an off-the-shelf
encoder and scores a query against it by cosine similarity.

In practice a frozen encoder lands on a newly onboarded place only when the
post's wording is close to the place name in embedding space, and the
gazetteer matches nothing shorter than the full name until someone adds
aliases.

## The five tabs

One row of tabs above the panels, one per function.

| Tab | What it takes | What it answers |
| --- | --- | --- |
| Post Geolocation | one post | the place the post is about, from the four post-level engines |
| User Geolocation | an account's recent posts | the place the account posts from, from the five user-level engines |
| Post vs. User Verification | both boxes | both of those, and the verification flag over the two consensus places |
| Cold-start Place Onboarding | a place name and a country or region | a drafted profile, and the place added to the catalogue |
| Bulk Evaluation | a labelled CSV | every engine scored over the rows, with a run manifest |

A tab sends only the boxes it shows, so the post tab sends no recent posts
and the user tab sends no post. Text typed in a box stays there while the
tabs change, and a scenario preset opens the tab each of its steps belongs
to.

## The result view

While the query runs the Result pane holds a placeholder row for each engine
that was asked, an elapsed counter and a line saying that the first query
after idle loads the encoders. Each engine's answer then appears with its
latency, its estimated cost where it calls a paid model, its provenance
(real, placeholder, call failed, not run or not selected) and its measured
Acc@1 on the WNUT-2016 run. The map pins the fused prediction at each level
and the consensus place beside it, and the verification flag fires when the
two consensus places are more than 161 km apart.

The flag's headline is the two places, the vote behind each and the distance
("Post: Singapore (3 of 4 engines). Timeline: Tokyo (4 of 4 engines). 5,311 km
apart."). Five candidate causes, what to check next and how the two places
were chosen are in the expandable note under it.

Between the two consensus places the map draws the great circle, sampled and
drawn in one longitude frame, so a Singapore to Tokyo pair is the 5,311 km
path the flag prints and a Tokyo to San Francisco pair crosses the Pacific
rather than Eurasia. The flag's radius is drawn as a geodesic circle, and
only when it is large enough at the current zoom to see. The legend lists
only what is on the map.

An engine is only run on text that it can read, and the rule applies in both
directions. A user-level engine with no timeline is not called, and a
post-level engine with no post is not called either. A query carrying only a
timeline therefore has no post-level fused prediction and no verification
flag, which is what stops the flag from comparing one body of text with
itself. Both sides of the rule live in `dispatch.py`, so the endpoint, the
batch runner and the CLI cannot drift apart.

When the gazetteer finds no catalogue place in a text, the engines still
answer, and the page says so once. The prediction is pinned in a muted
style, the map does not fly to it, the coordinate list tags it, and the
flag's note says that the gazetteer abstained.

## Scenario presets, and a run with no network

Three presets walk a scenario through its named steps, with Pause, Next step
and Restart while one runs and the current step printed beside them ("Step 3
of 6: the operator's edits"). A preset stops at the first step whose request
does not go through, says which step that was, and offers Retry.
A bar above those buttons names the scenario and whether it is in progress,
finished or stopped, shows how far through the steps it is, and offers Exit
scenario, which clears the scenario and returns to Post Geolocation.
A step opens the tab it belongs to: the viral-post check runs in three steps,
geolocating the post on Post Geolocation, the account's recent posts on User
Geolocation, and then the two together on Post vs. User Verification, where
the flag is raised.

Each tile also links to a recorded run. `?replay=<scenario id>` renders the
newest file in `docs/scenario-checks/` for that scenario through the ordinary
result view, behind a banner saying when and where it was recorded, and calls
no endpoint, so a venue with no network still has the demo.
`scripts/bundle_scenario_records.py` writes those files from the newest check
into `ui/static/scenarios/records/`. Run it and commit them after recording
a new check.

## Onboarding and the engines

Onboarding a place has its own tab, Cold-start Place Onboarding. It drafts a
Modular Retrieval profile (aliases, landmarks, foods, slang, notes, centroid)
and appends the name to the one catalogue list every engine holds. Engines
differ in how much of that profile they can read. For most of them
onboarding only makes the name selectable, and whether the engine then picks
it depends on the name itself rather than on the drafted fields.

| Engine | Chip in the interface | What it reads from the catalogue | What onboarding adds |
| --- | --- | --- | --- |
| ContrastGeo (post) | frozen encoder | Embeds the bare place name. | Candidate label only. No profile field reaches this engine. |
| FewUser (user) | frozen encoder | Embeds the template `a social media user from <name>`. | Candidate label only. |
| RetrieveZero (user) | frozen encoder | Embeds a passage built from the cached profile where the place has one, that is, its aliases, landmarks, foods and notes. Only an onboarded place has a profile, and for a built-in place the passage is the bare name. | Candidate label plus every MoR field, so re-drafting or editing the profile changes its answer. |
| Gazetteer (post and user) | string match | Counts occurrences of the place name, the cached aliases and, for an onboarded place, the cached landmarks in the text. A landmark counts a quarter of a name match and carries its own evidence string. | Candidate label plus aliases and landmarks. Without aliases it matches only the full name, so a post writing the place informally, `Bidadari` against `Bidadari Estate`, does not match. |
| GPT-4o-mini and Claude Haiku classifiers (post and user) | prompted LLM | The catalogue names are listed in the prompt, and a reply naming anything outside the list is dropped. | Candidate label only. |
| Map pin and distance metrics | | `coords_for` falls back to the onboarded centroid when a place is not in the built-in coordinate table. | The `lat`/`lon` in the profile. Without it the place cannot be pinned or scored for distance error. |

### The country or region hint

Onboarding takes an optional hint next to the place name, free text such as
`Singapore` or `West Kalimantan, Indonesia`. A bare place name is often
ambiguous, and the drafting model resolves the ambiguity without reporting
it, which `onboarding/regions.py` describes with a worked example. The hint
goes into the drafting prompt, is stored on the profile, and drives one extra
validation warning.

That warning is a rectangle test. `onboarding/regions.py` holds one bounding
box per country for the countries the default catalogue draws its cities
from, plus Indonesia and Singapore. Where the hint names one of them and the
drafted centroid falls outside its box, the panel says so before the operator
saves. The test is deliberately coarse. A box around Indonesia also contains
part of Malaysia, the box around the United States is drawn wide enough to
hold Alaska and Hawaii, and a hint that names a country the table does not
hold produces no warning rather than a guess. The check can therefore
identify a draft placed on the wrong continent, whereas a draft placed in the
wrong district passes it.

## Third-party requests

No script is loaded from a third-party host at run time, as Leaflet, its
stylesheet, its marker images and its licence are served from
`ui/static/vendor/leaflet/`. The basemap tiles are the exception, so a
visitor's browser contacts `tile.openstreetmap.org` as the map pans, and the
map credits "© OpenStreetMap contributors" with a link to the licence as the
ODbL requires. The local engine preset bounds what this server sends, not
what the basemap requests, and the page says so next to its privacy notice.
If the tiles or the script cannot load, the page still answers and lists
every prediction with its coordinate.

## Limits on the shared instance

A hosted instance serves one catalogue to everyone holding the link, so one
visitor's onboarding is the next visitor's candidate list. A built-in place
never leaves the catalogue. An onboarded one expires after an hour and its
profile is deleted with it. A place name, its aliases and its landmarks have
to read as names before they reach the prompts or the gazetteer. Editing or
removing a place can require the token its drafting returned, and there is a
ceiling on estimated spend, past which the paid engines stand down and the
local ones keep answering. `demo/deployment.md` lists every setting behind
those rules with its hosted value.

## Bulk evaluation

The Bulk Evaluation tab takes a CSV and scores all nine engines in one pass.
`eval/README.md` has the schema, the evaluation protocol, how the paper's
table was produced, and the shape of the two files the tab downloads, while
the OpenAPI document at `/docs` describes every field of both. Each of the
two files is self-contained: the per-row CSV carries each engine's predicted
place, its identifier, its coordinate and its distance error, while the
GeoJSON emits one feature per row, level and engine with one property schema
and a unique `Feature.id`.

## How the page is built

The page builds itself from `GET /instance`. Engine cards, chips, counts and
the "local engines only" preset all come from the server's roster, so an
engine registered in `engines/registry.py` appears without a line of client
code.

The interface is built for assistive technology as follows. The map is a
named group whose markers carry their own names and which points at the
coordinate list below it and at a hidden line of keyboard help with
`aria-describedby`. A verification flag is a
`role="status"` region under its own heading, and the results pane is not one
live region, so a query does not queue four thousand characters of
announcements. Focus moves to the answer when it arrives, to the first
drafted field when a profile comes back and to the message when something
fails, and stays put while the visitor is typing. The five tabs are a tablist
with arrow keys, and the four that share one panel name whichever of them is
selected. Every table has a caption and column and row headers, and there is
a skip link. A scenario declares the language of its
text, so the Indonesian crisis posts are marked `lang="id"` while they are in
the boxes.

On a phone, nothing scrolls sideways at 320 CSS pixels, wide tables scroll
inside their own container, and the waiting state is in the Result pane,
where the answer will be, rather than on a button below the fold.

`tests/test_browser_ui.py` drives all of that in Chromium against a
placeholder-mode instance, and `tests/test_contrast.py` computes every text
pair's contrast ratio from the stylesheet.

## Layout

| Part | Modules |
| --- | --- |
| Engine adapters behind one interface | `engines/`, `engines/base.py` |
| The one roster, and what `GET /instance` says about each engine | `engines/registry.py` |
| Which engines run for a given input | `dispatch.py` |
| Fusion within one level | `ensemble/` |
| The verification flag's per-level consensus | `triangulator/` |
| Cold-start onboarding | `onboarding/` |
| Bulk evaluation and metrics | `batch/`, `stats.py`, `geo.py` |
| Place-name normalisation and place identifiers | `places.py` |
| The list prices a cost is estimated from | `pricing.py` |
| The run manifest | `manifest.py` |
| What the verification flag measured, for the page to print | `flag_reference.py`, `data/` |
| HTTP API, request bounds and the one error envelope | `ui/server.py`, `ui/limits.py`, `ui/errors.py` |
| Vendored Leaflet, served by this instance | `ui/static/vendor/leaflet/` |

See `docs/adding-an-engine.md` for the `Engine` contract and how to
register one, `eval/README.md` for the API, the CSV schema and the
evaluation protocol, `CHANGELOG.md` for what has changed and what the API's
stability promise covers, and `demo/deployment.md` for running the demo and
every setting of the hosted instance.

## Adding an engine

`docs/adding-an-engine.md` is the walkthrough. It covers the `Engine`
contract, the four states a `Prediction` can be in, how to declare that an
engine sends text to a third party, the one-entry registration in
`engines/registry.py`, and how to test it. A registered engine appears on
the three geolocation tabs, in the bulk run, in the `engines` selection on
every endpoint, in the run manifest and in `GET /instance` with its own label
and tooltip, with no change to the server or the page.

## Use it as a library

```python
from geolens.dispatch import run_engines
from geolens.engines import GazetteerEngine
from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines.base import GeolocateInput
from geolens.ensemble import ensemble
from geolens.triangulator import triangulate

catalogue = list(DEFAULT_CITIES)
engines = {
    "gazetteer_post": GazetteerEngine(granularity="post", cities=catalogue),
    "gazetteer_user": GazetteerEngine(granularity="user", cities=catalogue),
}
granularities = {name: e.granularity for name, e in engines.items()}

payload = GeolocateInput(
    post="Massive fire at Marina Bay Sands in Singapore",
    user_posts=["Ramen in Tokyo again", "Tokyo is cold tonight"],
)
per_engine = run_engines(engines, payload, k=5)
fused = ensemble(per_engine, granularities, target="post")
flag = triangulate(per_engine, engines=granularities)

print(fused.consensus_city, flag.disagreement_flag, flag.disagreement_km)
```

Add the other adapters from `geolens.engines` to the dict to run them too.
`run_engines` applies both input rules and takes an optional `selected` set:
a user-level engine given no `user_posts` is not called, a post-level engine
given no `post` is not called, and an engine outside `selected` is not
called. Each comes back as a skipped prediction carrying the reason.

## Development

```bash
make test       # pytest
make lint       # ruff check
make typecheck  # mypy
make format     # ruff format
```

`tests/test_browser_ui.py` needs Playwright and a Chromium build
(`pip install -e ".[dev]"` then `python3 -m playwright install chromium`).
It starts its own placeholder-mode instance on a port between 7890 and 7899
with its own cache directory, so it does not disturb a server running on
7860. Without Playwright the module is skipped and the rest of the suite
runs as before.

`scripts/capture_ui.py` writes the paper's figure panels at device scale 3,
sized against the figure's height budget. It hides elements so the panels fit
and adds no text to the page, and its docstring lists every element it
hides.

```bash
python3 scripts/capture_ui.py --print-figure \
    --base-url https://kwanhui-geo-lens.hf.space \
    --out-dir figures
python3 scripts/capture_ui.py --print-figure-cards \
    --base-url https://kwanhui-geo-lens.hf.space \
    --out-dir figures
```

`--print-figure` writes `ui-print-left.png` and `ui-print-right.png`, the map
over the verification flag and the top of the onboarding form.
`--print-figure-cards` writes `ui-print-left-cards.png`, the flag's headline
box over the post-level engine cards of the same query, and
`--print-map-panel` writes `ui-print-map.png`, the map with its heading and
legend.

## Companion paper

"GeoLens: A Workbench for Comparing Zero-Shot Social Media Geolocation
Engines", by Kwan Hui Lim, Menglin Li, Kunrong Li, Roy Ka-Wei Lee and
Zhu Sun, submitted to the WSDM 2027 Demonstrations Track. The source code
is at
[`public-geoLens-WSDM2027`](https://github.com/kwanhui/public-geoLens-WSDM2027),
and the demo video is at <https://youtu.be/ArKLLrxWXCk>.

## Citation

The paper is under submission, so cite it as unpublished work until a decision
is in.

```bibtex
@unpublished{Lim2027WSDM_GeoLens,
  title  = {GeoLens: A Workbench for Comparing Zero-Shot Social Media Geolocation Engines},
  author = {Lim, Kwan Hui and Li, Menglin and Li, Kunrong and Lee, Roy Ka-Wei and Sun, Zhu},
  note   = {Submitted to the WSDM 2027 Demonstrations Track},
  year   = {2026},
}
```

## License

See [LICENSE](LICENSE).
