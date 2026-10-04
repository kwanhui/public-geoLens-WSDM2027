# Deployment notes

One machine, one process, no multi-tenancy and no persistence beyond the
container's own filesystem. The scope is a demonstration that can be cloned,
started with one command and used in a browser.

For a public URL the target is **Hugging Face Spaces** (Docker SDK).

## Local (developer machine)

```bash
git clone https://github.com/kwanhui/public-geoLens-WSDM2027.git
cd public-geoLens-WSDM2027
python3 -m venv venv && source venv/bin/activate
pip install -e ".[real]"
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
make demo
```

Open <http://localhost:7860>.

`make install` installs the base package, which runs the gazetteer for real
and everything else as a placeholder. The `real` extra is what pulls torch
and transformers, so the three encoder engines answer. **The first real query
downloads about 5 GB of encoder weights and takes one to two minutes.**
Later queries are fast. Pre-warm before a talk by sending one query.

`GEOLENS_CACHE_DIR` sets where the encoder embeddings and the onboarded
profiles are written; it defaults to `~/.geolens`. Point two instances at two
directories and they see neither each other's onboarded places nor each
other's embedding cache, which matters when a demo instance and an
evaluation run share a machine.

## The two keys, and capping both

`OPENAI_API_KEY` drives the GPT-4o-mini classifier at both levels and the
onboarding drafter. `ANTHROPIC_API_KEY` drives the Claude Haiku classifier at
both levels. Cap both, using a project-scoped OpenAI key and a
workspace-scoped Anthropic key, each with a hard monthly limit in the
provider's dashboard.

Claude is most of a query's cost. On the committed WNUT-2016 run the Claude
Haiku 4.5 engines cost about eight to nine times the GPT-4o-mini engines at
the same row count, so an Anthropic cap set too high is where a public URL
would drain. The figures in this repository are estimates from list prices,
not from a bill; see `eval/README.md`.

## Settings

Per-request size caps apply everywhere, including a local run. Everything
else is a deployment setting the package leaves off, so a local run paying
its own bill is not capped. The Dockerfile that builds the hosted image sets
the rest.

| Setting | Package default | Hosted | What it does |
|---|---|---|---|
| `MAX_BATCH_ROWS` | 50 | 50 | Rows per bulk run |
| `MAX_BATCH_BYTES` | 200,000 | 200,000 | Bytes per request body, uploads and JSON alike |
| `MAX_QUERIES_PER_HOUR` | 0, no limit | 30 | `/geolocate` and `POST /onboard` per address |
| `MAX_BATCHES_PER_HOUR` | 0, no limit | 5 | Bulk runs per address, counted after validation |
| `MAX_PROFILE_SAVES_PER_HOUR` | 0, no limit | 60 | `PUT` and `DELETE /onboard` per address |
| `GEOLENS_MAX_USD_PER_HOUR` | 0, off | 2 | Estimated spend for the whole process in a rolling hour |
| `GEOLENS_MAX_USD_PER_DAY` | 0, off | 10 | The same over a rolling day |
| `GEOLENS_ONBOARD_TTL_MINUTES` | 60 | 60 | How long an onboarded place stays in the shared catalogue |
| `GEOLENS_MAX_ONBOARDED` | 20 | 20 | How many are held at once, oldest evicted first |
| `GEOLENS_RECONCILE_SECONDS` | 300 | 300 | How often the profile cache is reconciled with the registry |
| `GEOLENS_REQUIRE_REGION` | 0 | 1 | Refuse a place name with no country or region hint |
| `GEOLENS_REQUIRE_EDIT_TOKEN` | 0 | 1 | `PUT` and `DELETE /onboard` need the token `POST /onboard` returned |
| `GEOLENS_OPERATOR_TOKEN` | unset | a Space secret | A token that may edit or purge any place |
| `GEOLENS_REFUSE_ADDRESS_LIKE` | 0, warn | 1 | Refuse a name that reads as a building or a street address |
| `GEOLENS_TRUSTED_PROXY_HOPS` | 0 | 1 | How many proxies sit in front, for reading `X-Forwarded-For` |
| `GEOLENS_CACHE_DIR` | `~/.geolens` | `/app/.geolens` | Where profiles and embeddings are written |
| `GEOLENS_STUB_MODE` | 0 | 1 in the image, 0 as a Space variable | Placeholder mode |
| `GEOLENS_LOG_SALT` | new each start | unset | Salt for the hashed address in the onboarding log line |

When a spend ceiling is reached the engines that call a paid model are
skipped with a plain reason and the local engines keep answering, so the page
still works; `GET /instance` and every inference response carry the state.

The instance logs one line per onboarding: the time, the place identifier,
the region hint and a salted hash of the client address. The address itself
is never written. Rate-limit windows hold an address only while it has a call
inside the hour.

## Hugging Face Spaces

### One-time setup

1. Create the Space at <https://huggingface.co/new-space>: owner `kwanhui`,
   name `geo-lens`, SDK `Docker`, visibility Public, so a visitor can reach
   it without an account.
2. Set the secrets and variables at *Space, Settings, Variables and
   secrets*: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and
   `GEOLENS_OPERATOR_TOKEN` as secrets, and `GEOLENS_STUB_MODE=0` as a
   variable to turn on real inference. Everything in the table above is in
   the Dockerfile, and a Space variable overrides one of them.
3. Cap both providers in their own dashboards.

### What the Dockerfile does

It builds a slim Python 3.11 image, installs the package with the `real`
extra, bakes the commit in, sets the table above, and runs
`python -m geolens.app --host 0.0.0.0 --port $PORT`. HF Spaces routes traffic
to `$PORT=7860`, which the Space frontmatter `app_port: 7860` matches.
`.dockerignore` trims the context: no tests, no docs, no demo media.

The image ships `GEOLENS_STUB_MODE=1` so the Space starts fast on a cold
boot, and relies on the Space variable to turn on real inference. The server
writes its mode in the first log line it emits, so the Logs tab says which
one is running before anybody clicks anything.

### What the platform can see

Hugging Face terminates TLS and proxies to the container, so the platform
sees every request's address, path, timing and status, and the Space's Logs
tab holds whatever the process writes to stdout. The posts a visitor types
are in the request bodies the container receives; the process does not write
them to the log, but a platform operator has the same view of the traffic
that any hosting provider does. Nothing is written to disk except onboarded
profiles and the encoder cache, both inside the container, both gone when it
restarts. Real data should therefore not be pasted into a hosted instance,
and the "local engines only" preset bounds what leaves this server, whereas
it does not bound what the platform sees.

The Space sleeps after 48 hours idle. The first hit after that is a cold
start of about 34 seconds with all nine engines; a warm query is about four
seconds.

## Running the demo at a venue

Run it locally on your own hotspot with your own keys. Conference networks
block outbound HTTPS to model providers often enough that a live hosted demo
is a coin toss, and the hosted instance is shared with whoever else is
holding the link.

- Start the local instance and send one query before the session, so the
  encoder weights are already downloaded and the first click is fast.
- Keep the recorded video and the committed scenario records under
  `docs/scenario-checks/` open in a tab, as they are the offline fallback.
  The records say what each engine answered on each scenario, before and
  after onboarding, against a real instance.
- On the hosted instance the budgets are per address and a venue shares one.
  One sweep of the three scenario presets costs about 11 of the 30 query
  units an hour, so three people at the booth exhaust the hour. That is
  another reason to run locally and leave the hosted URL for remote
  visitors.
