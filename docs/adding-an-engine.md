# Adding an engine

An engine is one geolocation method behind one `predict` call. Adding one
means writing an adapter of roughly fifty lines and one entry in the roster.
This page is the contract that adapter has to satisfy, where to register it,
and how to test it.

Nothing here needs a change to the server, the fusion, the verification flag
or the page. An engine the roster holds appears on the three geolocation
tabs, in the bulk run, in the `engines` selection on every endpoint, in the
run manifest and in `GET /instance`, with its own label and tooltip.

## The contract

Subclass `geolens.engines.base.Engine` and implement `predict`.

```python
from geolens.engines.base import Engine, GeolocateInput, Prediction


class MyEngine(Engine):
    name = "my_method"          # the family name, used in logs and notes
    granularity = "post"        # "post" or "user"
    needs_credentials = False   # True if it needs an API key or a download
    calls_a_third_party = False # True if a call sends the text off this server

    def __init__(self, *, cities: list[str], stub: bool | None = None) -> None:
        super().__init__(stub=stub)
        self.cities = cities

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        ...
```

`granularity`, `needs_credentials` and `calls_a_third_party` decide how the
workbench treats the engine.

`granularity` says which text it reads. A `post` engine is called only when
the input carries a post, and a `user` engine only when it carries a
timeline. `geolens.dispatch` applies both rules, so an engine never has to
guess. Give an engine that works at both levels two roster entries, as the
gazetteer and the two classifiers have.

`needs_credentials` says whether the engine can answer without an API key or
a downloaded model. Placeholder mode exists so that a keyless clone still
runs, and an engine that needs no credentials should serve its real answer
in that mode, which is what `needs_credentials = False` does. Read
`self.stub` in `predict` and return `geolens.engines._stubs.stub_predict`
when it is set.

`calls_a_third_party` says whether a call sends the input text off this
server. It is what puts an engine in or out of the "local engines only"
preset and what `GET /instance` reports, so an analyst who may not send text
to a third party can see and select on it. Set it True for anything that
makes a network call to a vendor. Declare what the call sends in
`sampling_parameters` too, read off the constants the call actually uses, so
the run manifest cannot name a setting the request does not carry.

`cities` is the one mutable catalogue list every engine is handed. Read it
inside `predict` rather than copying it, because a place onboarded at run
time is appended to that list and has to be a candidate on the next request,
with no rebuild or restart. An engine that caches anything keyed on the
catalogue must key the cache on the place set, as `engines/_encoder.py` does.

## The `Prediction` fields that matter

`predict` returns one `geolens.engines.base.Prediction`, which can be in any
of four states, each reported differently.

An answer sets `city` to a name from `self.cities`, exactly as it is spelled
there, `confidence` to the score for that place, and `top_k` to the ranked
`(place, score)` list. The fusion reads the whole list, so return `k`
entries when the method has them. `evidence` is a short human-readable
basis, shown on the card, and the gazetteer puts the matched toponym there.
An engine must not invent a place. A method that produces a label outside
the catalogue must drop it, as both classifiers do with a reply that names
anything not in the prompt's list.

An abstention means the method ran and found no usable location signal. Set
`abstain=True`, `city=""` and `top_k=[]`. Do not pad the list and do not
return the first catalogue entry at a uniform score, because an abstention
that names a place reads as a low-confidence prediction to any client that
ignores the flag, and it scores chance hits against that place. An abstained
row is excluded from the distance metrics and counted separately.

A failed call is one that was configured for live inference and did not
return a usable answer. Return `geolens.engines.base.failed_prediction(...)`
with the exception class. It carries no place and no score, and is excluded
from the fusion, from the per-level consensus and from the verification
flag. A failure is not a placeholder, as a placeholder city would enter the
fusion as an unrelated place and the engine would be reported as real.

A prediction that was not run is decided by the dispatcher and not by the
adapter. An engine whose level has no text, or that the caller did not
select, comes back from `geolens.engines.base.skipped_prediction(...)` with
the reason.

`mode` is derived rather than set. It reads `skipped`, then `failed`, then
whether `note` begins with "stub", and is `real` otherwise, so the one thing
an adapter must get right is the `note` prefix, `real:` or `stub:`. The run
manifest reports `mode` per engine, and a batch manifest reports the counts
per mode, so a placeholder can never be mistaken for a real number.

`cost_usd` is an estimate from the listed prices in `geolens.pricing`, never
a figure read from a bill. Call `geolens.pricing.estimate_cost(model, in,
out)`, which returns None for a model the table does not list, and an engine
whose price is unknown should report no cost and leave `cost_is_estimated`
False rather than borrow another model's rates. Add the model to
`geolens.pricing.PRICES` and move `PRICE_TABLE_RECORDED` if you want a cost.

## Registering it

One entry in `geolens/engines/registry.py`:

```python
EngineSpec(
    key="my_method_post",       # the name every endpoint and export uses
    label="My Method (post)",   # what the interface prints
    family="my_method",         # groups the levels of one method together
    granularity="post",
    tag="frozen encoder",       # the short pill beside the name
    tag_title="One sentence saying what actually runs here.",
    factory=lambda cities: MyEngine(cities=cities),
),
```

That is the whole registration. `build_engines()` is the one roster, which
the server, the CLI and `eval/adapters/run_wnut_eval.py` all call, and
`GET /instance` serves the label, the tag, the tooltip, the granularity, the
family and `calls_a_third_party` from it, so no client needs a table of its
own. Export the class from `geolens/engines/__init__.py` if callers should
be able to import it directly.

The tag has to describe what runs. "frozen encoder" means an off-the-shelf
encoder scoring a query against a string built from the place name, with no
support examples. Do not chip an engine "few-shot" unless something in the
interface or the API accepts support examples.

## Testing it

`tests/test_engine_registry.py` adds a toy engine and checks it end to end
without putting it in the shipped roster. Copy that pattern: register the
spec with `monkeypatch` for the test, then assert the engine

- is in `build_engines()` and holds the same catalogue list as everything else;
- answers a query through `POST /geolocate` with a place from the catalogue;
- abstains the way the contract says, with no place and an empty list;
- is skipped with the right reason when its level has no text;
- can be named in `engines` and comes back "not selected" when another is;
- appears in `GET /instance` with a label, a tag and a tooltip.

Assert supersets, never the literal roster. A test that pins the nine
shipped names fails the moment anyone adds a tenth.

An adapter that calls a vendor also needs a test that the SDK contract it
targets is the one installed. `tests/test_llm_claude.py` is the example: it
asserts that the argument list the Claude adapter sends is the one the
installed `anthropic` major version accepts, so a version bump that removes
an argument fails the suite rather than falling back silently to a
placeholder.
