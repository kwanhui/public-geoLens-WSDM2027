.PHONY: install test lint typecheck format demo clean

# --- Setup ---
install:
	pip install -e ".[dev]"

# --- Quality ---
test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src/

format:
	ruff format .

# --- Demo ---
# Local equivalent of the Hugging Face Space (Docker SDK).
DEMO_CMD ?= python3 -m geolens.app --host 0.0.0.0 --port 7860

demo:
	$(DEMO_CMD)

# --- Cleanup ---
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
