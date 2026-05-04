# ::] UNIMATRIx

A simulated society of LLM-driven agents. Each agent has a personality, a
role (president, banker, scholar, worker, beggar...), and a social class.
They talk to each other, form opinions, change their minds, and vote on
proposals that can move them between classes. The goal is to watch what
emerges — coalitions, mobility, polarization — without scripting it.

A web control panel runs the show: you start the process, pick a config,
hit **Start**, watch the conversations / votes / social graphs tick
forward, and **Stop** when you've seen enough. Past runs stay browsable
from the same UI.

![Unimatrix web UI](res/img/ss.png)

## What you need

- Python 3.11 or newer
- Either a **GPU + local model** (recommended), or just the **stub backend**
  if you only want to see the wiring run. The stub produces deterministic
  fake replies — no LLM, no GPU, no network.

## Run it (stub, no GPU)

```bash
# from the repo root
py -3 -m venv .venv
.venv/Scripts/activate           # Windows
# source .venv/bin/activate      # Linux/macOS

pip install -e .

python -m unimatrix.main --backend stub
```

That starts the **control panel only** — no simulation is running yet.
Open <http://localhost:8001/>, pick a config from the dropdown
(`example_run.json`, `standard.json`, …), and click **Start simulation**.

Click **Stop simulation** to end the run. The control panel keeps running
so you can pick another config and start again, or browse past runs in
the Run manager. Press Ctrl-C in the terminal to shut the whole process
down.

Each run is saved under `runs/<name>_<timestamp>/` (SQLite DB + Chroma
store + matplotlib graphs) and registered in `runs/_registry.db`.

## Run it with a real model

The repo ships a small inference server under `inference_server/` that
downloads GGUF models from HuggingFace and serves them on an OpenAI-
compatible endpoint. It uses its own venv so its heavy GPU dependencies
don't pollute this one.

```bash
# 1. start the inference server (separate terminal, separate venv)
#    full instructions in inference_server/README.md
cd inference_server
py -3.12 -m venv .venv && .venv/Scripts/activate
pip install .
python download.py qwen2.5-3b           # ~2 GB, runs on CPU
python serve.py qwen2.5-3b              # serves on http://localhost:8000
```

```bash
# 2. in another terminal, from the repo root
.venv/Scripts/activate
python -m unimatrix.main \
    --backend llama_cpp \
    --endpoint http://localhost:8000
```

The control panel comes up; pick a config and Start. The `--backend` /
`--endpoint` / `--model` CLI flags are applied as **overrides** to
whichever config you pick at start time, so the same flags work whether
the config defaults to a different backend.

You can also point Unimatrix at any OpenAI-compatible endpoint (vLLM, an
OpenAI-API-compatible cloud, etc.) with `--backend vllm --endpoint <url>`.

## Configuration

Everything about a run lives in a single JSON file in the configs
directory (default: `config/`). Two starters ship with the repo:

- `config/standard.json` — 30 agents, balanced default for a smoke test.
- `config/example_run.json` — 50 agents, fuller demographics.

Copy either one and edit. Any `*.json` file dropped into the configs
directory shows up in the UI dropdown on the next page refresh.

Useful CLI flags (all optional):

| Flag | Effect |
|------|--------|
| `--configs-dir DIR` | Where to look for config files (default: `config`) |
| `--backend stub\|llama_cpp\|vllm` | Override `inference.backend` at start time |
| `--endpoint URL` | Override `inference.endpoint` |
| `--model NAME` | Override the model name sent to the endpoint |
| `--host 127.0.0.1` | Web UI bind host |
| `--port 8001` | Web UI port |
| `--runs-dir runs` | Where per-run artifacts go |
| `--log-level info` | Uvicorn log level |

The control panel's **Recent events** box mirrors the orchestrator's
terminal log line-by-line — the same human-readable feed, no JSON dump.

## Optional extras

```bash
# real embeddings (Chroma + sentence-transformers) instead of the stub embedder
pip install -e ".[embed]"

# dev tools (pytest)
pip install -e ".[dev]"
pytest
```

## Layout

```
src/unimatrix/
  config/         pydantic schema + JSON loader
  persistence/    SQLite stores + run registry
  memory/         short / medium / long-term + per-person impressions
  inference/      HTTP client (vLLM / llama.cpp / stub)
  agents/         agent runtime, system prompts
  conversations/  1-to-1, group, broadcast
  voting/         proposals, mandatory votes, tally
  orchestrator/   main loop, social-need decay, anti-silence trigger
  graphs/         matplotlib renderers
  web/            FastAPI control panel + HTML UI
  session.py      simulation lifecycle (start / stop, one orchestrator)
  log_console.py  Rich console that mirrors log lines into the UI
  main.py         CLI entry point (starts the web server only)
config/           ships with example configs
inference_server/ bundled local llama.cpp host (own venv)
runs/             per-run artifacts + registry (gitignored)
tests/            pytest suite (uses stub backend)
```
