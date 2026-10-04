# Changelog

Notable changes to GeoLens, newest first. The build version is
`geolens.__version__`, read by `pyproject.toml` and reported by the FastAPI
app and every run manifest.

The HTTP contract carries its own version, `api_version`, reported by
`GET /instance` and in every manifest. Within one major version a field may
be added and an optional parameter may appear; no field is removed or
renamed. Anything outside the OpenAPI document may change in any release.

## 0.14.5 (2026-09-20), API version 1.0

The viral-post scenario runs in three steps. Only the page changed.

- Checking a viral post geolocates the post on Post Geolocation, the account's recent posts on User Geolocation, and then the two together on Post vs. User Verification, where the flag is raised. Each step sends only the boxes its own tab shows and opens the engine group it is about.
- The strip above the scenario buttons reads "Demonstration: Try a scenario".
- When the only prediction is one whose text named no catalogue place, the map shows that place's region at a wide zoom, where it used to stay on the previous query's place.
- A result expands only the engine groups whose level the query ran. A group whose engines were stood down stays closed and says ", not run" beside its count, and the two explanations, "How the fused prediction is computed" and the flag's candidate causes, are closed again on every new answer.

## 0.14.4 (2026-09-20), API version 1.0

A loaded scenario says so in words and can be left at any point.

- A bar above the scenario buttons names the scenario and its state: in progress, finished or stopped. A step bar beside the step line shows how far through it is, and the tile of the loaded scenario carries the same state in words.
- Exit scenario clears the scenario's text and its result, un-presses the tile and returns to Post Geolocation. It works during a step, between steps, after the last step and after a stop, and on a recorded run it leaves the recorded run.
- A place a scenario onboarded is not removed by leaving it. The landing view says the place is still onboarded and keeps the Reset onboarding button beside the line.

## 0.14.3 (2026-09-20), API version 1.0

One tab per function. The page opens on Post Geolocation, and the row above
the panels holds User Geolocation, Post vs. User Verification, Cold-start
Place Onboarding and Bulk Evaluation. The engines, the scoring and the API
did not change.

- Each geolocation tab shows only the boxes that its task reads, and sends only those. Text typed in a box is kept when the tab changes.
- Each tab opens with one line and an info note that names the engines which answer it. The verification tab says that the flag needs both boxes.
- Onboarding has its own tab, with a note on how a place is drafted from a name and a country and on what each engine then reads.
- A scenario and its walkthrough open the tab that each step belongs to, and choosing a tab by hand ends a running walkthrough.
- The run line above a result names the task that produced it.
- The tab row wraps at phone widths.

## 0.14.2 (2026-09-19), API version 1.0

The interface shows less text. Labels are short, and each longer explanation
sits behind an info button next to the label or value that it explains; the
button opens the explanation inline, by click or keyboard. No explanation was
removed.

- The line under the verification flag says what its rate counts: "Flagged 60% of home-consistent accounts on WNUT-2016".
- Each bulk table has a note that lists its column glosses, which a touch screen never shows as tooltips, and the tables name the engines as the cards do.
- A refusal for an over-long alias or landmark names the field that it came from.
- A level whose engines were not run is left out of the reach summary.
- The footer, the README and `CITATION.cff` give the paper's title as "GeoLens: A Workbench for Comparing Zero-Shot Social Media Geolocation Engines".
- `eval/README.md` is shorter, and the settings tables now live only in `demo/deployment.md`.

## 0.14.1 (2026-09-19), API version 1.0

Everything since 0.8.0, in preparation for the WSDM 2027 submission. The
HTTP contract gained fields and one status code and lost nothing, so the API
version is still 1.0.

### Added

- `api_version`, separate from the build version, on `GET /instance` and in every manifest.
- Engine selection on both query endpoints and both CSV forms, recorded as `selected_engines`.
- `flag_radius_km`, default 161, range 1 to 20,015, and `--flag-radius-km` on the CLI.
- `GET /catalogue`, `GET /onboard/status`, `DELETE /onboard` and `catalogue_status`.
- `ground_truth_user_city`, so `/eval` scores each level against its own truth.
- `place_id`, `place_coordinates`, `places`, `feature_type`, `centroid_source`, `error_km` and `within_161km`.
- `on_row_error`, whose `skip` returns the bad row with `status: "error"` and runs the rest.
- `outcome`, where `no_catalogue_place` marks a live reply that named nothing in the catalogue.
- `engine_reference` and `flag_reference` on `GET /instance`, read from the committed WNUT-2016 run.
- `edit_token` on `POST /onboard`, required by `PUT` and `DELETE` where the deployment says so.
- An estimated-spend ceiling per hour and per day, and a per-address budget for profile saves.
- `distance_method`, `price_table`, `cost_is_estimated`, `git_commit`, `catalogue_sha`, `engine_call_counts` and the gazetteer abstention counts.
- `built_in_places_present` on `GET /healthz`, and `warnings` on a batch response.
- Landmark matching in the gazetteer, for onboarded places only, at a quarter of a name match.
- A country or region hint on onboarding, checked against one bounding box per country.
- Three scenario presets with Pause, Next step, Restart and Retry, and a recorded run at `?replay=<scenario id>`.
- A waiting state inside the Result pane, character counts, and a "Data handling" disclosure.
- Vendored Leaflet, so no script is loaded from a third-party host at run time.
- `docs/adding-an-engine.md`, `CHANGELOG.md`, `CITATION.cff`, `requirements-lock.txt` and the scripts under `scripts/`.
- Browser and contrast tests driving a placeholder instance in Chromium.

### Changed

- One error envelope everywhere, every status code declared in the OpenAPI document, unknown request fields refused, and non-finite numbers refused.
- A CSV is decoded as `utf-8-sig`, ground truth is normalised before matching, and CRLF and quoted newlines parse as one row per record.
- A place name, alias or landmark is bounded and has to read as a name before it reaches either prompt or the gazetteer.
- A reply's confidence is clamped to [0, 1], and a non-finite value drops the entry.
- A request holds the catalogue steady for its whole engine run and its manifest.
- The byte and character caps apply to the JSON batch endpoints too, and a bulk run is counted only after validation.
- An unlimited budget sends no `X-RateLimit` header, and an empty window is discarded.
- The page builds its engine cards, chips, counts and presets from `GET /instance`.
- The map draws great circles, a geodesic flag radius and one longitude frame.
- Both downloads stand alone, and the CSV's `*_ensemble_*` columns became `*_fused_*`.
- Accessibility and reflow: a named map group, a `role="status"` flag, a tablist, no sideways scroll at 320 CSS pixels, every text pair above 4.5:1.
- The research engines are chipped "frozen encoder", and one vocabulary runs across the interface, the exports and the docs.
- The Docker image bakes the build commit in and sets the hosted limits.

### Fixed

- `PUT /onboard` edits and does not create, and an unknown name is a 404.
- A built-in place stays in the catalogue, and an overlay on one is tracked separately and discarded on its own.
- An expired place is deleted, as the profile cache is reconciled with the registry at startup and on a timer.
- A profile is keyed on the place identifier, so two names sharing no ASCII letters no longer share a cache file.
- Two Unicode spellings of one name are one place, and a different spelling of one already held is a 409.
- A duplicate or empty row id is refused, and an `/eval` call with no ground truth is a 422.
- No post means no post-level output, so the flag cannot compare a timeline with itself.
- The Claude Haiku 4.5 rate was the Haiku 3.5 price, so every Claude cost reported was 0.8 of the correct figure. An unlisted model now reports no cost.
- The eval summary reported the static catalogue size after an onboarding.
- The WNUT-2016 shared task's authors are Han, Rahimi, Derczynski and Baldwin, and the FewUser adapter runs SimCSE-RoBERTa-large.

### Evaluated behaviour

The prompt text, the encoder scoring, the per-level consensus rule, the
161 km default, the fusion arithmetic and its default method, the
gazetteer's matching of built-in names and aliases and the distance formula
are as they were at tag `wnut2016-eval-3803853`. Three inputs to the engines
are not:

- The gazetteer returns only the places the text named, where the list was padded up to k and an abstention returned the first catalogue entry. The top-1 place and score on a match stay the same, but fused rankings and the per-level consensus can change.
- A failed engine call carries no place and is scored nowhere, where it used to return a placeholder place that was scored.
- Landmark matching reaches onboarded places only, so no committed number moves.

`eval/README.md` sets out what each one moves, and the paper discloses the
two that move a committed number.

## 0.8.0 (2026-05-30)

Feature and interface work for the first public release.

- A centroid captured at onboarding, so a new place pins on the map and enters the distance metrics.
- One shared mutable catalogue handed to every engine and appended on `/onboard`.
- Distance-aware consensus-to-consensus disagreement with a 161 km gate.
- `BannerMetrics` precision and recall against a `should_disagree` column.
- `Prediction.mode`, `.abstain` and `.evidence`, gazetteer abstention, and `engine_modes` in the manifest.
- A run manifest on every live response, a JSON export, `compute_rollup` and a GeoJSON export.
- Inline errors, `aria-live` results, a map text summary, a responsive layout and a privacy notice.

## 0.7.0 (2026-05-27)

- Distance metrics: haversine in `geo.py`, catalogue centroids in `engines/_coords.py`, median and mean great-circle error and Acc@161km.
- 95% Wilson intervals on Acc@1 and Acc@5, and the closed-set candidate count `N` in the summary.
- Reciprocal Rank Fusion as a selectable `ensemble_method`.
- Per-difficulty stratified Acc@1, and a per-row CSV export carrying each engine's top-1.
- The run manifest: tool version, model ids, catalogue size and hash, k, fusion method and timestamp.

## Earlier

0.1.0 to 0.6.0 built the workbench: the `Engine` interface and the first
three adapters, the per-level consensus, the cold-start onboarding wizard,
the FastAPI server and the map-anchored page, Hugging Face Spaces
deployment, and real inference on every engine.
