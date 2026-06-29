<div align="center">

# ::] UNIMATRIx v2.0.1

**A sandbox for watching what AI agents actually become.**

A simulation where LLM-driven *beings* are born into a bare world with almost no
rules, and must author their own identities and their own society. There is no scripted hierarchy, economy, or government.

![The UNIMATRIx control panel](res/img/ss.png)

</div>

---

## The idea

If you give AI agents only existence and finitude — and take everything else away, **who do they decide to be, and what do they build together?**

Each being starts as a *thin seed*, from there it writes and rewrites
its story, values, beliefs, goals, bonds, and how it faces its own ending. 

## How the world works

The engine imposes only a minimal substrate and then gets out of the way.

| Law | What it means |
| --- | --- |
| ⏳ **Finitude** | Every being has **vitality** that falls each tick. At zero it **dies** — permanently, never to act again. Time is scarce and its length unknown. |
| 🌾 **Sustenance** | Vitality is restored only by **sustenance**, and sustenance comes only from **work**. Survival depends on labor. |
| 🗣️ **Speech** | The one way to reach another being is to **speak**. Every relationship, idea, and institution that arises does so because someone made it with words. |
| 🪞 **The self** | Each being holds an evolving **self-model** it authors from experience, and may become someone wholly different from who it began as. |
| 📜 **The commons** | A shared, public space where beings inscribe ideas for all to see — the medium for emergent culture. |

Beyond speaking, on any tick a being may take **one open action**:

```
work           — work the patch you stand on; a co-present crew each draws more
move           — step toward a patch within sight (richer ground lies elsewhere)
express        — voice an idea into the common world (a belief, name, story, norm…)
bond           — form or deepen a typed tie (friend, ally, rival, mentor, partner, kin)
dissolve       — break a tie
share          — give some of your sustenance to another
bear_successor — with a trusted partner, create a new being who inherits part of you both
rest           — do nothing
```

Out of these primitives, five dimensions of life **emerge and are recorded**:

- 🪞 **Self-evolution** — every rewrite of a being's identity is versioned; you can
  watch, diff by diff, a being *become itself*.
- 💡 **Meaning & belief** — ideas are authored into the commons, then **adopted,
  remixed, and transmitted** between beings, with full lineage.
- 🔨 **Labor on the land** — beings **harvest** the patches they stand on; ground
  depletes as it is worked and regrows slowly, and a co-present crew each draws more
  than a lone worker — the only source of the sustenance that keeps them alive.
- 🤝 **Kinship & intimacy** — durable, typed **relationships**.
- ⚰️ **Mortality & continuity** — real **death**, and **succession**: bonded partners
  bear successors that inherit a blend of their selves, their memories, and their
  culture — producing lineages and evolution across generations.

## Quick start

Requires **Python 3.11+**.

```bash
# from the repo root
py -3 -m venv .venv
.venv/Scripts/activate            # Windows
# source .venv/bin/activate       # Linux / macOS

pip install -e .
```

**Run with no model (stub backend)** — deterministic fake replies, no GPU, no
network. Good for seeing the machinery turn:

```bash
python -m unimatrix.main --backend stub
```

Open <http://localhost:8001/>, pick `standard.json` from the **start** dropdown,
and click **start**. Stop anytime; the panel keeps running so you can start
again or browse past runs.

**Run with a real model.** Point `inference.endpoint` at any OpenAI-compatible
chat server — [LM Studio](https://lmstudio.ai/), vLLM, llama.cpp, … — in
`config/standard.json`, then simply:

```bash
python -m unimatrix.main
```

Overrides without editing the config: `--backend`, `--endpoint`, `--model`,
`--host`, `--port`.

## Configuration

A config is one JSON file in `config/`. Its blocks:

| Block | Controls |
| --- | --- |
| `simulation` | name, RNG seed, tick interval, checkpoint cadence |
| `inference` | LLM backend, endpoint, model, token / concurrency limits |
| `memory` | short / medium / long-term sizes, embedding model |
| `social` | the connection drive that keeps the world from going silent |
| `world` | **the substrate** — vitality & death, the spatial grid of patches beings work (`world.ecology`), the sustenance / labor economy, succession, and the self-revision cadence |
| `messaging` | message caps, reflection cadence |
| `agents` | the beings' thin seeds: `id`, `name`, `gender`, `circumstance`, `disposition` |

A seed is deliberately minimal — diversity without a script:

```json
{ "id": "agent_05", "name": "Eskar", "gender": "m",
  "circumstance": "You woke certain only that you would one day end.",
  "disposition": "A preoccupation with what lasts." }
```

Set `world.blank_slate: true` to start every being identical (no seed at all) —
a control for studying pure emergence.

## What gets recorded

Every run is a self-contained **SQLite** database under `runs/`, alongside a
Chroma vector store for memory, and registered in `runs/_registry.db`. It keeps
the full history — self-model versions, words, the patches beings work, cultural
artifacts and their adoptions, relationships, lineage, deaths, and the
public event log — which is everything the panel and the analysis read.

`analysis_scripts/analyze_run.py` turns a run DB into a four-dimension
`metrics.json` + markdown digest:

```bash
python analysis_scripts/analyze_run.py runs/<run>.db
```

## Project layout

```
src/unimatrix/
  config/         configuration schema + loader
  agents/         the Agent, its self-model, and all prompt builders
  orchestrator/   the tick loop, action interpreter, finitude & self-revision
  messaging/      asynchronous speech + reflection
  memory/         short / medium / long-term memory + impressions
  inference/      LLM client (OpenAI-compatible + a stub backend)
  persistence/    SQLite schema, store, and run registry
  web/            FastAPI server + the single control-panel page
  session.py      run lifecycle      ·   main.py  entry point
config/           simulation configs
analysis_scripts/ off-line analysis of a run
```

## License

See [LICENSE](LICENSE).
