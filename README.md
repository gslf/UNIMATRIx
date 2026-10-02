# UNIMATRIx v4.0.0

UNIMATRIx evaluates one candidate model in deterministic, 72-tick simulated societies across eight domains. The bundled `standard-v1` and `validation-v1` recipes contain 16 and 128 episodes respectively.

## Install and run

Python 3.11 or newer is required.

```bash
uv sync --locked --extra dev
uv run unimatrix serve
```

Open `http://127.0.0.1:8001`, add a model in **Model settings**, select a recipe, and start a run. The dashboard stores runs under `runs/benchmarks` and model configurations under `config/models`.

```bash
uv run unimatrix recipes
uv run unimatrix estimate --recipe standard-v1 --seconds-per-decision 15
uv run unimatrix estimate --recipe validation-v1 --seconds-per-decision 10
uv run unimatrix run --model config/models/my-model.json --recipe standard-v1
uv run unimatrix replay --run runs/benchmarks/RUN_ID/episodes/EPISODE_ID
```

A run can be resumed only with the exact recipe and engine revision that created it. Existing data is not migrated between releases.

## Operations

The dashboard binds to loopback by default. Remote access requires TLS, an explicit allowed host, and a token of at least 32 characters supplied through `UNIMATRIX_DASHBOARD_TOKEN`. Back up completed episode databases with `unimatrix backup`; verify them with `unimatrix replay`.

Local model files, credentials, authored recipes, and run data are excluded from Git and release artifacts. Only `standard-v1` and `validation-v1` are bundled.

## Development

```bash
uv run pytest
uv run ruff check src/unimatrix tests --exclude tests/fixtures
uv build
```

See [recipe and model configuration](docs/benchmark.md) and [recipe lab](docs/recipe-lab.md). Licensed under [MIT](LICENSE).
