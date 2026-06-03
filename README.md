# GeoLens

**A Workbench for Comparing and Ensembling Few-Shot and Zero-Shot Social Media Geolocation Methods**

Kwan Hui Lim, Menglin Li, Kunrong Li, Roy Ka-Wei Lee, Zhu Sun.

Live demo: <https://kwanhui-geo-lens.hf.space>.

GeoLens puts several social-media geolocation methods behind one map-anchored
interface so they can be compared and combined on the same input. It wraps three
research methods (post-level few-shot, user-level few-shot, and zero-shot
LLM-retrieval) and three public baselines (a string-match gazetteer, GPT-4o-mini,
and Claude Haiku) behind a common `Engine` interface.

On top of the engines, GeoLens fuses the predictions of each granularity by a
weighted sum or by reciprocal rank fusion and reports the gap against the best
single engine. It flags, with a distance-aware score, a post-level prediction
that disagrees with the user-level one, for example a post that looks local
from an account whose history points elsewhere. A new city can be onboarded
from LLM-drafted, editable knowledge fields without retraining, and a CSV can
be scored against every engine and ensemble for label accuracy, distance error
with confidence intervals, and per-row results.

In the hosted demo the three research engines run as frozen-encoder baselines;
the gazetteer runs locally and the two LLM engines call their respective APIs.

## Install

```bash
git clone https://github.com/kwanhui/public-geoLens-WSDM2027.git
cd public-geoLens-WSDM2027

python3 -m venv venv
source venv/bin/activate
make install
```

The two LLM engines need API keys. Set them in a local `.env` (never commit it):

```bash
echo "OPENAI_API_KEY=sk-..."        >  .env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
export $(grep -v '^#' .env | xargs)
```

Without keys, the gazetteer and the frozen-encoder engines still run; the LLM
engines are skipped.

## Run the demo (UI)

```bash
make demo
```

Open <http://localhost:7860>. Pick a city, paste a post or a user's recent
posts, and click *Geolocate*. The map shows each engine's prediction with its
confidence and the ensemble consensus.

## Run from the command line

```bash
python3 -m geolens.cli geolocate --post "Just landed at Changi, ready for kaya toast"
python3 -m geolens.cli onboard --city "Singapore"
```

## Bulk evaluation

A small example set ships in `eval/example_test_set.csv`. See
[`eval/README.md`](eval/README.md) for the CSV schema, the metrics reported, and
how to convert external benchmarks (e.g. WNUT-2016) into the same schema.

```bash
python3 -m geolens.cli eval eval/example_test_set.csv --out results.json
```

## Reproducing the WNUT-2016 evaluation

`eval/wnut2016_id_manifest.csv` holds the tweet IDs and gold labels used in the
paper's preliminary evaluation. Twitter/X terms permit redistributing tweet IDs
only, so the rehydrated text CSVs are not included; regenerate them from your own
licensed WNUT-2016 copy with `eval/adapters/wnut2016_to_geolens.py`, then score
with `eval/adapters/run_wnut_eval.py`. Aggregate metrics and the run manifest are
committed under `eval/results/`.

## Development

```bash
make test       # pytest
make lint       # ruff check
make typecheck  # mypy
make format     # ruff format
```

## Citation

If you use this software, please cite it as:

```bibtex
@software{Lim2026GeoLens,
  title   = {GeoLens: A Workbench for Comparing and Ensembling Few-Shot and Zero-Shot Social Media Geolocation Methods},
  author  = {Lim, Kwan Hui and Li, Menglin and Li, Kunrong and Lee, Roy Ka-Wei and Sun, Zhu},
  year    = {2026},
  version = {0.8.0},
  url     = {https://github.com/kwanhui/public-geoLens-WSDM2027},
}
```

## License

MIT. See [LICENSE](LICENSE).
