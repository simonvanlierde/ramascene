# Install the venv from uv.lock.
py-sync:
    uv sync

# What CI runs: lint, format check, types, tests.
py-check:
    uv run ruff check
    uv run ruff format --check
    uv run ty check
    uv run pytest

# Fix what ruff can fix, then format.
py-fmt:
    uv run ruff check --fix
    uv run ruff format

# Tests with coverage: a terminal summary, plus coverage.xml for an uploader.
py-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=xml

# Tests only; extra arguments reach pytest (`just py-test -k name`).
py-test *args:
    uv run pytest {{ args }}

# Serve on 127.0.0.1:8020 (HOST and PORT override), outside Docker.
serve:
    uv run ramascene-jobs
