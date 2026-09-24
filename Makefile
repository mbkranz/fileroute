.PHONY: help install sync sync-dev lint format test check docs-sync docs-update docs-serve docs-build docs dry-run clean

PYTHON ?= python
UV ?= uv
UVX ?= uvx
DESCRIPTOR ?= resources/descriptor.yaml
SHAREDRIVE_ARGS ?= pull $(DESCRIPTOR)

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Useful commands:\n"} /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install sharedrive as a uv tool from this checkout.
	$(UV) tool install .

sync: ## Install runtime dependencies.
	$(UV) sync

sync-dev: ## Install development and test dependencies.
	$(UV) sync --group dev --group test

lint: ## Run Ruff lint checks.
	$(UV) run ruff check .

format: ## Format Python files with Ruff.
	$(UV) run ruff format .

test: ## Run the test suite.
	$(UV) run pytest

check: lint test ## Run lint and tests.

docs-sync: ## Install documentation dependencies.
	$(UV) sync --extra docs

docs-update: ## Regenerate CLI/API markdown docs.
	$(UV) run $(PYTHON) scripts/update_docs_markdown.py

docs-check: ## Check that generated CLI/API markdown docs are current.
	$(UV) run $(PYTHON) scripts/update_docs_markdown.py --check

docs-serve: ## Serve documentation locally.
	$(UV) run mkdocs serve

docs-build: ## Build the documentation site.
	$(UV) run mkdocs build

docs: docs-update docs-build ## Regenerate and build documentation.

dry-run: ## Run a safe sharedrive dry-run command; override SHAREDRIVE_ARGS.
	$(UVX) sharedrive $(SHAREDRIVE_ARGS) --dry-run

clean: ## Remove local Python/test/doc build caches.
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov site
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
