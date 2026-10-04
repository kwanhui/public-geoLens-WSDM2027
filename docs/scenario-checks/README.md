# Scenario checks

This directory holds dated records of what the shipped demo scenarios did on
a running instance. The paper cites results from them, and
`scripts/recompute_table.py` reads the newest one.

## What a record is

One JSON file per check, named for the date it was taken
(`2026-09-19.json`). Each file holds:

| field | what it is |
|---|---|
| `generated_at` | when the check ran, UTC |
| `base_url` | the instance it ran against |
| `tool.commit` | the repository commit the instance was expected to be running, and `tool.dirty` if the checkout had uncommitted changes. The ids refer to the development repository: `2026-09-19.json` was taken on 0.14.2 and `2026-09-20.json` on 0.14.5 |
| `scenarios` | one entry per preset: its id, the state before onboarding where the scenario has one, the drafted profile and its warnings, the operator's edits, and for every post each engine's place, confidence, mode and abstention, both fused predictions, and the verification flag with its separation in km and its notes |

A record describes one scenario at one commit against one instance, and
nothing in the repository asserts that a scenario produces a particular
place, because the LLM engines can answer differently on two calls.

## Which command writes one

```bash
python3 scripts/check_scenarios.py --base-url https://kwanhui-geo-lens.hf.space
```

It reads the place, the region hint, the posts and the operator's edits from
the scenario files under `src/geolens/ui/static/scenarios/`, which is the
same source the interface preset, the demo recorder and
`scripts/capture_ui.py` read, so a committed record matches what a visitor
sees. `--only <id>` checks one preset.

Run it against an instance that serves real inference. In placeholder mode
every engine returns a deterministic stand-in, so the places in the record
are not predictions and must not be cited in the paper. Every engine entry
carries `mode`, which reads `stub` throughout on such a run, and the script
reports the mode on stderr, so check `mode` before citing anything from a
record. The same applies to a record taken against an instance that was not
at the commit `tool.commit` names, or one taken with `tool.dirty` set.

## Keeping a record current

`scripts/recompute_table.py` warns when a scenario file under
`src/geolens/ui/static/scenarios/` was committed after the newest record's
`tool.commit`, since the record then describes an older scenario. It is a
warning and not a failure, because the fix is to re-record against a
deployed instance, which an offline script cannot do. Re-record after
deploying.
