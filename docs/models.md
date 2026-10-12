# Model configuration

A candidate model is one JSON object in `config/models/` (git-ignored). The dashboard's **Model
settings** creates and edits it, stores credentials in a separate `*-credentials.json` file with
mode `0600`, and tests the endpoint. The steward is called once per simulated week.

```json
{
  "model": "qwen3-27b",
  "snapshot": "qwen3-27b-q4_k_m@2026-09",
  "endpoint": "http://127.0.0.1:1234",
  "context_tokens": 16384,
  "budget_track": "accounted_compute",
  "temperature": 0.2,
  "structured_output": "decision_schema"
}
```

| Field | Meaning |
| --- | --- |
| `model`, `snapshot`, `endpoint` | Required. `snapshot` names an immutable build of the weights; it is recorded with every run. |
| `budget_track` | Required. `accounted_compute` refuses providers that do not report generated tokens; `opaque_compute` accepts them. |
| `context_tokens` | LM Studio only (`http://host:port`): the SDK loads or verifies the model with this context and refuses a mismatch instead of truncating. |
| `context_bytes_verified` | Any OpenAI-compatible endpoint without `context_tokens`: the context you verified, at least 24,000 bytes. |
| `managed_instance` | LM Studio: load a dedicated instance for the run and unload only that instance afterwards. |
| `max_input_tokens`, `max_output_tokens` | Optional budgets; an over-long prompt stops before generation. |
| `temperature`, `top_p`, `top_k`, `seed`, penalties | Sampling. Unset controls keep provider defaults; temperature defaults to 0. |
| `reasoning_effort` | `low`, `medium` or `high`, verified against the model's advertised capabilities (LM Studio). |
| `reasoning_delimiters` | Two tags (e.g. `["<think>", "</think>"]`) that split inline reasoning from the answer; requires `constrain_output: false`. |
| `structured_output` | `json_object` (default) or `decision_schema`: the provider constrains the answer to the steward's JSON schema. |
| `constrain_output` | `false` sends no response format at all, for providers that reject it. |
| `system_prompt`, `personality`, `persona` | Optional personality appended to the steward's static instructions (presets: `GET /api/personalities`). |
| `api_key_env` or `api_key_file` | Credentials from an environment variable or a separate file, never inline. |
| `request_timeout_seconds` | Whole-decision deadline (default 120 s); a timeout is a non-retryable infrastructure failure. |
| `model_sha256`, `backend_revision`, `quantization`, prices | Provenance and cost metadata, recorded with the run. |

Unknown fields are rejected. Provider errors are reported with credentials redacted; HTTP 429 and
5xx are retried up to three times; a run that still fails is marked *failed* with the error, and its
decision log lets it resume from the last recorded week.

## Parallel seeds

A recipe with several seeds (`hell-v1` plays three) can play them side by side: `--parallelism N`
on the command line, **Parallel seeds** in the dashboard. The run loads the model **once** and every
seed sends its weekly decision to that same instance, so memory does not grow with `N`:

- with `managed_instance`, the run loads its own instance on the first decision and unloads it when
  the run ends, pauses or fails;
- without it, the run uses the instance LM Studio already has for the model key (or lets LM Studio
  load it once); the first lookup or request is serialized, so parallel seeds never trigger two loads.

LM Studio 0.4 and later answers several predictions at once on one instance (continuous batching;
**Max Concurrent Predictions** in the model's load settings, 4 by default; further requests wait in
a queue). With the default unified KV cache the context is shared by the predictions in flight, so
`context_tokens` should cover `N` weekly prompts and answers at once (about `N × 4k` tokens, more for
reasoning models). If a seed fails, the others stop too and the run can be resumed.

Before a long run, record a health check and the runtime identity:

```bash
uv run unimatrix doctor --model config/models/my-model.json --output /tmp/model-health.json
```

It exits with status 1 when the model does not answer with a JSON object.
