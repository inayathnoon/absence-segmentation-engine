# Everything runs offline against synthetic data. No credentials, no cloud.
#
# `make demo` is the entry point: the whole pipeline on a 28-day slice, ending
# in the results table. If it breaks, nothing else here is worth reading.

PY := .venv/bin/python
DBT := ../.venv/bin/dbt
PROFILE ?= demo

.PHONY: setup data load dbt segment capacity reports charts pipeline demo dashboard dagster test lint types clean

setup:  ## Create the virtualenv and install exactly what uv.lock pins
	uv venv --python 3.11
	uv sync --frozen --extra dev
	.venv/bin/pre-commit install || true

data:  ## Generate the synthetic sources and the truth set
	ASE_PROFILE=$(PROFILE) $(PY) -m absence_engine.gen.run

load:  ## Load data/raw into DuckDB under contract
	ASE_PROFILE=$(PROFILE) $(PY) -m absence_engine.warehouse.loader

dbt:  ## Build the warehouse and run every dbt test
	cd dbt && DBT_PROFILES_DIR=. $(DBT) build

capacity:  ## Recoverable capacity and savings
	ASE_PROFILE=$(PROFILE) $(PY) -m absence_engine.capacity.model

grade:  ## Score the segmentation and the recovery against planted truth
	ASE_PROFILE=$(PROFILE) $(PY) -m absence_engine.capacity.grading

reports:  ## Render the per-role weekly reports to out/reports/
	ASE_PROFILE=$(PROFILE) $(PY) -m absence_engine.reporting.reports

charts:  ## Write the README charts to docs/img/
	ASE_PROFILE=$(PROFILE) $(PY) -m absence_engine.reporting.charts

pipeline: data load dbt charts reports  ## The full pipeline

demo:  ## Full pipeline on the small profile, with the results table
	$(MAKE) pipeline PROFILE=demo
	ASE_PROFILE=demo $(PY) -m absence_engine.reporting.results

dashboard:  ## Streamlit, with the role selector
	.venv/bin/streamlit run app/streamlit_app.py

dagster:  ## Dagster UI for the asset graph
	.venv/bin/dagster dev -m absence_engine.orchestration.definitions

backfill:  ## Re-materialise the daily segmentation partitions
	.venv/bin/dagster job backfill -m absence_engine.orchestration.definitions \
		--job segmentation_backfill --all

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests app
	.venv/bin/ruff format --check src tests app

types:
	.venv/bin/mypy src

clean:  ## Remove generated data, the warehouse, reports and dbt artefacts
	rm -rf data warehouse out dbt/target dbt/logs
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
