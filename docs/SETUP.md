# Setup

## Prerequisites

- **Python 3.11+** — check with `python3 --version`. The package declares
  `requires-python = ">=3.11"`.
- **git** — to clone the repository.
- **pip** — ships with Python.

The harness has **no third-party runtime dependencies** (`dependencies = []`
in `pyproject.toml`). The only extra you need is `pytest` for the test
suite.

## Clone and install

```bash
git clone https://github.com/tech-explained/agentic-sdlc-harness.git
cd agentic-sdlc-harness

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

`.[dev]` installs the package in editable mode plus `pytest>=8` (the
`dev` optional dependency in `pyproject.toml`). Editable mode registers the
`harness` console command (`harness = "harness.cli.main:main"`).

Prefer not to install? Every command below also works without installing:

```bash
PYTHONPATH=src python -m harness.cli.main <command>
PYTHONPATH=src python -m pytest tests -q
```

Run these from the repository root — `pytest` is configured with
`testpaths = ["tests"]`, and the CLI resolves `fixtures/` and `results/`
relative to the repo root by default.

## Verify the install

```bash
python -m pytest tests -q
```

Expected result: **65 passed** (48 unit + 17 integration). This exercises
the state machine, retry ceiling, gate ordering, all four contract rules,
the six fault scenarios, human checkpoint paths, crash-shaped replay, and
the subprocess CLI adapter.

Then confirm the CLI is wired up:

```bash
harness validate-fixtures
```

It should exit 0 with no errors, meaning the 12 task fixtures and 6 fault
manifests under `fixtures/` are well-formed.

## Troubleshooting

**1. `ModuleNotFoundError: No module named 'harness'` when running
`pytest` or the CLI.**

You are running from the repo root without the package installed, or the
virtualenv is not activated. Either install it (`pip install -e ".[dev]"`)
or prefix commands with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m pytest tests -q
```

**2. Install fails with "requires Python >=3.11" (or you see syntax
errors on import).**

Your default `python3` is too old. Point the virtualenv at a newer
interpreter explicitly:

```bash
python3 --version          # confirm what you have
python3.11 -m venv .venv   # or python3.12
source .venv/bin/activate
pip install -e ".[dev]"
```
