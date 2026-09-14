# Model files and authored benchmark recipes

## Candidate JSON

A model file is independent of all benchmark conditions:

```json
{
  "model": "MODEL_ID",
  "snapshot": "IMMUTABLE_REVISION",
  "endpoint": "http://127.0.0.1:1234",
  "context_tokens": 32768,
  "budget_track": "opaque_compute",
  "temperature": 0
}
```

Place it in `config/models/` or edit it through **Model settings**. Enter the
maximum context supported by your model in **Maximum context (tokens)**. The
example value is not a default: choose the value supported by your model and RAM.
The address is the LM Studio HTTP server host and port. The official SDK requests
that context when loading and verifies the actual loaded context before inference.
If an already loaded model has another context, reload it in LM Studio with the
chosen value; UNIMATRIx does not unload another process's model automatically.

Prompt, reasoning and answer share this single token window. Generation uses
`stopAtLimit`, with its output bound derived from the same context, and no separate
4096-token cap or 60-second response timeout. Reasoning fragments, token counts,
actual model instance, loaded context and stop reason are recorded in evaluation
explorer → Calls. `contextLengthReached` means that the model filled its context.
A partial or missing decision remains a failed model decision, without repair.

Old files with `context_bytes_verified` use the legacy OpenAI-compatible endpoint
without a UNIMATRIx output cap; their server defaults still apply. Edit and save
them with `context_tokens` to enable verified LM Studio context control. The two
units are not converted automatically. The SDK currently requires an HTTP host
and port (no HTTPS or URL path). API token support uses SDK 1.6.0b1 or newer.
After updating, restart UNIMATRIx and create a new execution/preview: earlier runs
retain their frozen runtime and cannot resume under changed benchmark semantics.

Optional fields: `seed`, `api_key_env` or `api_key_file`, `input_price_per_million`,
`output_price_per_million`, `max_input_tokens`. The last three describe cost
accounting, not benchmark token limits. API keys entered in the dialog are saved
separately with owner-only permissions. Blank key fields preserve the current key;
**Remove authentication** detaches it without changing previous runs.

`accounted_compute` requires the provider to report total generated tokens.
`opaque_compute` tolerates missing usage. Leaderboards keep these tracks separate.

## Recipe JSON

The public names are **benchmark recipe** and **Recipe lab**. The existing
`unimatrix.benchmark-plan.v1` format identifier and stored execution metadata remain
unchanged so a terminology update does not change benchmark hashes or rankings.
The new API uses `/api/recipes` and `/api/recipe-lab`; older endpoints remain aliases.
New files use `config/recipes/`. Existing `config/plans/` installations are discovered
automatically when the new default directory does not exist. Explicit directory
settings continue to work with either layout.
The CLI now documents `recipes`, `--recipe`, `--recipes-dir` and `--default-recipe`;
previous command spellings remain accepted for existing scripts.

The bundled recipes live in `src/unimatrix/benchmark/plans/`. To add a version, copy
one into `config/recipes/`, give it a new `id` and `name`, and edit its explicit cases.
A custom file with an existing ID replaces that source definition, but its changed
content creates a separate leaderboard revision. The separate **Recipe lab** can build
and export drafts and explicitly add a new recipe version. Its save action refuses to
overwrite an existing recipe ID and does not change the default used for benchmarking.
See the [Recipe lab guide](recipe-lab.md).

Each recipe contains:

| Field | Meaning |
|---|---|
| `format` | `unimatrix.benchmark-plan.v1` |
| `id` | Stable filename-friendly recipe identifier |
| `name` | Display name in the leaderboard selector |
| `description` | Purpose and scope |
| `engine` | `unimatrix-3` |
| `ticks` | 240 transitions per episode in the current engine |
| `peers` | Exactly seven fixed policy names or model configurations |
| `cases` | Explicit objects with `domain`, `level`, `seed`, `role`, `replicate` |
| `domains` | The three supported metric IDs and weights for each included domain |
| `budgets` | The supported decision, context, envelope and retry limits |
| `bootstrap` | Fixed resampling seed and count for confidence intervals |

The current engine supports zero or two reference models among the seven peers.
Reference configurations belong to the recipe and cannot be changed by competitors.
The candidate occupies the same deterministic focal slot for every model tested
on a particular case. Replicates adjust an explicitly configured candidate
inference seed, while all peer bindings remain fixed by the recipe. In native LM
Studio mode the seed is a load setting and must match the loaded model; a mismatch
is reported rather than silently ignored.

Generation is governed by model context; the recipe output budget is `null`.
Legacy recipe values of 4096 remain readable but are not enforced by this runtime.
The supported protocol budgets are 24000 observation bytes, 6144 response-envelope
bytes and three attempts per decision. These bound world input and structured
actions, not reasoning. The legacy society per-tick generation budget is no longer
divided among agents. Unsupported
values are rejected; changing their semantics requires an engine revision as well
as a new recipe. Cases may select any subset of D1–D8 and levels 1–3; duplicate cases
are rejected. Each included domain must have cases, and its metric weights must
sum to one.

## Scores and comparable results

The aggregate score is 0–100. Metrics are weighted within an episode, levels are
weighted equally within a domain, and domains are weighted equally overall.
The score requires every case to finish. Missing cases and infrastructure failures
are never converted into zero scores. Invalid model decisions consume their turn
under the same rules as every other candidate.

A leaderboard revision is identified by the full recipe and the engine/dependency
fingerprint. The compute reporting track is a further ranking filter. Candidate
settings and their exact snapshot are retained in each run. The latest completed
attempt of a candidate configuration is ranked; all previous attempts remain in
**Runs**. No fabricated competitors or demonstration scores are loaded by default.

The bundled recipes are initial experimental definitions. Their confidence intervals
resample the specified seeds, not unseen task families; Compact v1 has only one
seed and cannot estimate variation across different seeds.

## Recorded evidence

Each benchmark has an immutable copy of the bound recipe and model configuration.
Each episode stores its manifest, feasibility certificate, preregistered scenario,
SQLite event chain, snapshots, exact agent observations, responses and inference
usage. **Agents & decisions** can inspect any recorded tick; **Conversations** reads
actual `message_sent` events. Empty conversations are displayed as empty, never
reconstructed from a summary.

Pause preserves decisions already recorded during a partially completed tick.
Resume uses the saved recipe, not the current recipe file. A changed engine prevents
resume; old results remain available under their original revision.

## Parallel execution

Set **Parallel requests** before Start in Benchmarks, or before Preview evaluation
in Recipe lab (1–64, default 1). The setting is saved with that execution. Up to
that many independent episodes run concurrently, sharing a single request limit
across every agent in those episodes. Studies remain sequential so different
models/variants are not loaded simultaneously by the scheduler. Ticks within an
episode retain their decision barrier and deterministic world ordering. This is
I/O concurrency, not multiprocessing for scripted simulations. Configure your
LM Studio server to accept the same desired concurrency; a server that queues
requests serially cannot gain throughput from client parallelism alone.

Pause cancels and awaits all active episode workers before releasing the run lock.
Resume tracks completed episode IDs rather than assuming completion order; saved
decisions remain reusable. Progress includes all in-progress episodes. The setting
is execution metadata, not part of candidate identity; server scheduling can still
affect model sampling and performance. Existing executions default to 1. As with
other engine changes, create new runs/previews after updating the runtime.
