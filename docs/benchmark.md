# Recipes and model configuration

A recipe fixes the episode cases, peer policies, scoring weights, decision budgets, and wall-time limit. The bundled recipes are:

| Recipe | Episodes | Ticks each | Wall-time limit |
| --- | ---: | ---: | ---: |
| `standard-v1` | 16 | 72 | 26 hours |
| `validation-v1` | 128 | 72 | 52 hours |

Both cover D1–D8. `standard-v1` tests each domain under standard and stress conditions, with one role per condition. `validation-v1` crosses both conditions and both roles in each domain, with four independent seeds in every cell. Run `unimatrix estimate` with measured seconds per decision before starting a provider-backed run. The estimate assumes sequential provider calls and applies a configurable safety factor; the 52-hour Validation limit fits the default 1.5 safety factor at a measured ten seconds per decision, but not at every model latency.

Model configuration is a JSON object in `config/models`. The dashboard can create it, check endpoint health, and save credentials separately. Use a local LM Studio server or an OpenAI-compatible endpoint. Keep configuration files and credentials outside source control. Before a run, `unimatrix doctor --model config/models/MODEL.json --output /tmp/model-health.json` checks the configured inference transport and records runtime identity; it exits with status 1 when the model health check fails.

A run stores its frozen recipe, runtime fingerprint, episodes, and report in `runs/benchmarks`. An interrupted run resumes only with matching runtime and recipe identity. Changing a recipe requires a new recipe ID and a new run. Episode databases can be checked with `unimatrix replay`.

The reported rating is a signed gain against per-case scripted reference anchors. It may be below zero or above one. A complete set of valid reference anchors is required for a rating and leaderboard entry. The ranking panel also reports paired raw-metric aggregate intervals and a separate strict casewise-win certificate.
