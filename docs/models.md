# Model configuration

A candidate model is one JSON object in `config/models/` (git-ignored). The dashboard's **Model
settings** creates and edits it, stores credentials in a separate `*-credentials.json` file with
mode `0600` on POSIX or a protected, current-user-only ACL on Windows, and tests the
endpoint. Permissions are applied when the temporary file is created, before writing
the key, and retained through atomic replacement. The Windows implementation uses
[CreateFileW security attributes](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew);
[Python's Windows `chmod`](https://docs.python.org/3/library/os.html#os.chmod) cannot
enforce POSIX access bits. The steward is called once per simulated week.

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
| `temperature`, `top_p`, `top_k`, `seed`, penalties | Sampling. Unset controls keep provider defaults; temperature defaults to 0. For OpenAI-compatible APIs, explicit `temperature: null` omits it (blank in the editor). |
| `reasoning_effort` | `low`, `medium` or `high`, verified against the model's advertised capabilities (LM Studio). |
| `reasoning_delimiters` | Two tags (e.g. `["<think>", "</think>"]`) that split inline reasoning from the answer; requires `constrain_output: false`. |
| `structured_output` | `json_object` (default) or `decision_schema`: the provider constrains the answer to the steward's JSON schema. |
| `constrain_output` | `false` sends no response format at all, for providers that reject it. |
| `system_prompt`, `personality`, `persona` | Optional personality appended to the steward's static instructions (presets: `GET /api/personalities`). |
| `api_key_env` or `api_key_file` | Credentials from an environment variable or a separate file, never inline. |
| `request_timeout_seconds` | Whole-decision deadline (default 28,800 s = 8 h); a timeout is a non-retryable infrastructure failure. |
| `model_sha256`, `backend_revision`, `quantization`, prices | Provenance and cost metadata, recorded with the run. |
| `openrouter` | OpenRouter-only routing and reasoning options, forwarded as `provider` and `reasoning` in the request. See below. |

Unknown fields are rejected. Provider errors include the response details with credentials
redacted. HTTP 429 and 503 receive up to six retries, with increasing waits from 15 seconds to
5 minutes plus jitter, respecting `Retry-After` (seconds or an HTTP date). Retries stop when the
next wait would exceed a 15-minute elapsed budget; a longer server delay is reported instead
of retrying early. Known credit/quota exhaustion errors are not retried. Health checks report
errors immediately. Other transient failures retain the runner's three-attempt limit. A run
that still fails is marked *failed* with the error, and its decision log lets it resume from
the last recorded week. Successful calls record HTTP retry count and wait time.

## OpenRouter

Use `endpoint: "https://openrouter.ai/api"` and the **OpenAI-compatible API** backend.
The transport appends `/v1/chat/completions`; do not include `/v1` in the configured endpoint.
Use `context_bytes_verified`, not LM Studio's `context_tokens`. Add the API key in the model
editor and save; credentials remain separate from the configuration and reports.

To pin an endpoint and enable thinking, add this configuration (provider slugs come from
OpenRouter's public model endpoints API):

```json
{
  "openrouter": {
    "provider": {
      "only": ["deepinfra/fp8"],
      "allow_fallbacks": false,
      "require_parameters": true,
      "quantizations": ["fp8"]
    },
    "reasoning": {"enabled": true, "exclude": false, "effort": "high"}
  }
}
```

The editor preserves these options when adding a key or changing other fields, and displays
the chosen provider and reasoning status. `only` restricts routing; an unavailable provider
fails instead of silently selecting another. The optional `quantizations` filter affects routing;
the top-level `quantization` field is descriptive only. For providers whose precision is unknown,
omit the filter and record `quantization: "unknown"` rather than claiming BF16.

`reasoning.enabled: true` requests thinking. `effort` is optional and must be supported by the
selected model/provider; omitting it keeps the provider default. `reasoning.exclude: false`
keeps available reasoning in the response. For example, the Anthropic Haiku endpoint does not
accept temperature: use `temperature: null` instead of asking OpenRouter to silently ignore it.
This is different from the LM Studio-only top-level `reasoning_effort` field.

Use `budget_track: "accounted_compute"`. The output cap includes reasoning as well as the final
answer on most providers; 65,536 tokens leaves margin over the recorded Flash runs. The
connection test for explicitly enabled OpenRouter reasoning allows up to 8,192 tokens (or the
configured output cap, if smaller) and 120 seconds. It reports output exhaustion separately
from a malformed JSON response. Health checks are paid inference requests too.

Logs record the requested routing/reasoning, returned provider name, request ID, and provider
usage (including reported cost). The snapshot field records the observed catalog version;
it does not pin an upstream checkpoint. Prices in configurations are estimates, may include
promotions, and do not override the provider's billing.

References: [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

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
