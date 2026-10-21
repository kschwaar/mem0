.PHONY: format sort lint

# Variables
ISORT_OPTIONS = --profile black
PROJECT_NAME := mem0ai

# Default target
all: format sort lint

install:
	hatch env create

install_all:
	pip install ruff==0.16.0 groq together boto3 'litellm>=1.83.7,<1.98.0' ollama chromadb weaviate weaviate-client sentence_transformers vertexai \
	            google-generativeai elasticsearch opensearch-py vecs "pinecone<7.0.0" pinecone-text faiss-cpu langchain-community \
							upstash-vector azure-search-documents langchain-memgraph langchain-neo4j langchain-aws rank-bm25 pymochow pymongo psycopg kuzu databricks-sdk valkey

# Format code with ruff
format:
	hatch run format

# Sort imports with isort
sort:
	hatch run isort mem0/

# Lint code with ruff
lint:
	hatch run lint

docs:
	cd docs && mintlify dev

build:
	hatch build

publish:
	hatch publish

clean:
	rm -rf dist

test:
	hatch run test

test-py-3.10:
	hatch run dev_py_3_10:test

test-py-3.11:
	hatch run dev_py_3_11:test

test-py-3.12:
	hatch run dev_py_3_12:test

# Focused offline/loopback regressions; no running Mem0 or model service required.
PYTHON ?= python3
PNPM ?= pnpm
QUICK_LOOPBACK_ARTIFACTS ?= /tmp/mem0-quick-loopback
QUICK_LOOPBACK_REPORT_DIR := $(abspath $(QUICK_LOOPBACK_ARTIFACTS))
QUICK_LOOPBACK_PYTEST_ARGS ?=
QUICK_LOOPBACK_VITEST_ARGS ?=

.PHONY: test-quick-loopback test-quick-loopback-python test-quick-loopback-node test-quick-loopback-plugin test-quick-loopback-server test-quick-loopback-dashboard

test-quick-loopback:
	@status=0; \
	for suite in python node plugin server dashboard; do \
		$(MAKE) --no-print-directory test-quick-loopback-$$suite || status=1; \
	done; \
	printf '\nQuick-loopback reports: %s\n' "$(QUICK_LOOPBACK_REPORT_DIR)"; \
	exit $$status

test-quick-loopback-python:
	@mkdir -p "$(QUICK_LOOPBACK_REPORT_DIR)"
	PYTHONPATH="$(CURDIR)/cli/python/src" $(PYTHON) -m pytest \
		-c cli/python/pyproject.toml cli/python/tests/test_quick_loopback.py \
		-ra --junitxml="$(QUICK_LOOPBACK_REPORT_DIR)/python-cli.xml" $(QUICK_LOOPBACK_PYTEST_ARGS)

test-quick-loopback-node:
	@mkdir -p "$(QUICK_LOOPBACK_REPORT_DIR)"
	$(PNPM) --dir cli/node exec vitest run tests/quick_loopback.test.ts \
		--reporter=default --reporter=junit \
		--outputFile="$(QUICK_LOOPBACK_REPORT_DIR)/node-cli.xml" $(QUICK_LOOPBACK_VITEST_ARGS)

test-quick-loopback-plugin:
	@mkdir -p "$(QUICK_LOOPBACK_REPORT_DIR)"
	$(PYTHON) -m pytest integrations/agent-plugin-core/tests/test_quick_loopback.py \
		-ra --junitxml="$(QUICK_LOOPBACK_REPORT_DIR)/plugin.xml" $(QUICK_LOOPBACK_PYTEST_ARGS)

test-quick-loopback-server:
	$(MAKE) --no-print-directory -C server test-quick-loopback \
		QUICK_LOOPBACK_ARTIFACTS="$(QUICK_LOOPBACK_REPORT_DIR)" \
		QUICK_LOOPBACK_PYTEST_ARGS="$(QUICK_LOOPBACK_PYTEST_ARGS)"

test-quick-loopback-dashboard:
	@mkdir -p "$(QUICK_LOOPBACK_REPORT_DIR)"
	node --test --test-reporter=spec --test-reporter-destination=stdout \
		--test-reporter=junit --test-reporter-destination="$(QUICK_LOOPBACK_REPORT_DIR)/dashboard.xml" \
		server/dashboard/tests/*.test.mjs
