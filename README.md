# ::] UNIMATRIx

A simulated society of ~50 LLM-driven agents. Each agent has a personality,
a role (president, banker, scholar, worker, beggar...), and a social class.
They talk to each other, form opinions, change their minds, and vote on
proposals that can move them between classes. The goal is to watch what
emerges — coalitions, mobility, polarization — without scripting it.

A web UI shows the conversations, votes, and social graphs in real time as
the simulation ticks forward.

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

python -m unimatrix.main --config config/example_run.json --backend stub
```

Open <http://localhost:8001/> in your browser.

Press Ctrl-C to stop. Each run is saved under `runs/<name>_<timestamp>/`
(SQLite DB + Chroma store + matplotlib graphs).

## Run it with a real model

The repo ships a small inference server under `inference_server/` that
downloads GGUF models from HuggingFace and serves them on an OpenAI-compatible
endpoint. It uses its own venv so its heavy GPU dependencies don't pollute
this one.

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
    --config config/example_run.json \
    --backend llama_cpp \
    --endpoint http://localhost:8000
```

You can also point Unimatrix at any OpenAI-compatible endpoint (vLLM, an
OpenAI-API-compatible cloud, etc.) with `--backend vllm --endpoint <url>`.

## Configuration

Everything about a run lives in a single JSON file. `config/example_run.json`
is a complete starting point: 50 agents, 15 roles, 4 classes, the social
dynamics tuning, the inference settings. Copy it and edit.

Useful CLI overrides:

| Flag | Effect |
|------|--------|
| `--backend stub\|llama_cpp\|vllm` | Override the config's backend |
| `--endpoint URL` | Override the inference endpoint |
| `--model NAME` | Override the model name sent to the endpoint |
| `--port 8001` | Web UI port |
| `--runs-dir runs` | Where per-run artifacts go |

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
  web/            FastAPI + HTML UI
  main.py         CLI entry point
config/           example run config
inference_server/ bundled local llama.cpp host (own venv)
runs/             per-run artifacts (gitignored)
tests/            pytest suite (uses stub backend)
```
