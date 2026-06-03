# Hugging Face Spaces (Docker SDK) image for GeoLens.
# Local equivalent: `make demo` after `make install` in a venv.
FROM python:3.11-slim

WORKDIR /app

# Install only what the runtime needs (no dev deps).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# `.[real]` pulls torch + transformers so that the engines can load their
# pre-trained encoders when GEOLENS_STUB_MODE=0. With stub mode on the demo
# works without these, but we ship them so flipping the env var on the Space
# flips the engines from stubs to real predictions without a Docker rebuild.
RUN pip install --no-cache-dir -e ".[real]"

# Pre-warm the HuggingFace transformers cache directory so model downloads
# land here, not in /root/.cache (HF Spaces wipes home between restarts).
ENV HF_HOME=/app/.hf-cache

# HF Spaces (Docker SDK) routes traffic to $PORT; default 7860 to match
# the Space frontmatter `app_port: 7860`.
ENV PORT=7860
EXPOSE 7860

# Reduce demo cost when running on a public URL (RetrieveZero engine calls LLM).
ENV MAX_USD_PER_SESSION=0.20
ENV MAX_QUERIES_PER_HOUR=30

# Default to stub mode so the Space starts fast on cold-boot. Flip
# GEOLENS_STUB_MODE=0 (or unset) in the Space's Variables tab to turn on
# real encoder-similarity inference. First request will download ~5 GB of
# encoder weights and take 1-2 minutes; subsequent requests are fast.
ENV GEOLENS_STUB_MODE=1

CMD ["sh", "-c", "python -m geolens.app --host 0.0.0.0 --port ${PORT}"]
