# Hugging Face Spaces (Docker SDK) image for GeoLens.
# Local equivalent: `make demo` after `make install` in a venv.
FROM python:3.11-slim

WORKDIR /app

# Install only what the runtime needs (no dev deps).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# `.[real]` pulls torch + transformers so that the engines can load their
# pre-trained encoders when GEOLENS_STUB_MODE=0. With stub mode on the demo
# works without these, but they ship here so flipping the Space variable
# turns on real inference without a Docker rebuild.
#
# The install reads pyproject.toml, so the version ranges declared there are
# the ones the Space gets. Pin new requirements there, not here, or the image
# and a local `make install` drift apart.
RUN pip install --no-cache-dir -e ".[real]"

# The build context carries no .git, so the commit is baked in here and every
# manifest and `GET /instance` reports it. A plain `docker build` can pass
# `--build-arg GEOLENS_GIT_COMMIT=$(git rev-parse HEAD)`.
ARG GEOLENS_GIT_COMMIT=""
ENV GEOLENS_GIT_COMMIT=${GEOLENS_GIT_COMMIT}

# Pre-warm the HuggingFace transformers cache directory so model downloads
# land here, not in /root/.cache (HF Spaces wipes home between restarts).
ENV HF_HOME=/app/.hf-cache

# Where onboarded profiles and the encoder cache are written. It is inside
# the container, so a restart starts from the built-in catalogue.
ENV GEOLENS_CACHE_DIR=/app/.geolens

# HF Spaces (Docker SDK) routes traffic to $PORT; default 7860 to match
# the Space frontmatter `app_port: 7860`.
ENV PORT=7860
EXPOSE 7860

# Reduce demo cost when running on a public URL (the LLM engines are paid
# calls). The three per-hour budgets are deployment settings and the package
# applies none of them by default, so a local run is not capped; this is the
# deployment that sets them. Saving and deleting a profile have their own
# budget, because an operator who has spent the inference budget still has to
# be able to correct or remove a bad draft that every other visitor can see.
ENV MAX_QUERIES_PER_HOUR=30
ENV MAX_BATCHES_PER_HOUR=5
ENV MAX_PROFILE_SAVES_PER_HOUR=60

# One ceiling for the whole process, beside the per-address budgets. The
# figures are estimates from the list prices in the run manifest. While a
# ceiling is reached the paid engines are skipped and the local engines keep
# answering, so the page still works.
ENV GEOLENS_MAX_USD_PER_HOUR=2
ENV GEOLENS_MAX_USD_PER_DAY=10

# Spaces terminates TLS and proxies to the container, appending the address it
# saw to X-Forwarded-For. Trusting exactly that one hop is what lets the rate
# limiter see the real client; with 0 (the default, for a direct local run)
# the header is ignored, so a client cannot forge its way past the cap.
ENV GEOLENS_TRUSTED_PROXY_HOPS=1

# The catalogue is shared by everyone using the Space, so an onboarded place
# expires and the number held at once is capped, oldest evicted first. The
# cache directory is reconciled against the registry on the same clock, so an
# expiry is a deletion.
ENV GEOLENS_ONBOARD_TTL_MINUTES=60
ENV GEOLENS_MAX_ONBOARDED=20
ENV GEOLENS_RECONCILE_SECONDS=300

# A bare place name is ambiguous, and the drafting model resolves it without
# saying so. On a shared instance that costs every later visitor, so the hint
# is required here even though a local run only warns.
ENV GEOLENS_REQUIRE_REGION=1

# The catalogue is shared, so editing or removing a place needs the token its
# drafting returned. Set GEOLENS_OPERATOR_TOKEN as a Space secret to hold a
# key that can purge anything.
ENV GEOLENS_REQUIRE_EDIT_TOKEN=1

# This instance geolocates posts to neighbourhoods and towns. A name that
# reads as a building or a street address is refused rather than warned about.
ENV GEOLENS_REFUSE_ADDRESS_LIKE=1

# Default to stub mode so the Space starts fast on cold-boot. Set
# GEOLENS_STUB_MODE=0 in the Space's Variables tab to turn on real encoder and
# LLM inference. The first real request downloads about 5 GB of encoder
# weights and takes one to two minutes; later requests are fast. The mode is
# in the first log line the server writes.
ENV GEOLENS_STUB_MODE=1

CMD ["sh", "-c", "python -m geolens.app --host 0.0.0.0 --port ${PORT}"]
