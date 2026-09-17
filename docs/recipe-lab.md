# Recipe lab

Open **Recipe lab** from the benchmark dashboard, or visit `/recipe-lab`. This is the
operator's separate workspace for developing benchmark recipes. The page and its
documentation are in English. **Guide** opens the complete manual; the **Info**
buttons open the relevant section. The manual documents every field, all 24 metric
weights, all nine peer policies, intervention combinations, findings and workflows.

## Build, test, inspect, version

1. Start from a named recipe, enter a new recipe ID and name, then select domains,
   scenario seeds, starting roles and inference replicates.
   Reserve a separate holdout seed set. Choose 7–127 peers, each with a scripted policy or a saved model. Model peers
   have individual personality names and editable system prompts, with presets.
   Advanced settings select the three metric weights within each domain.
2. **Build draft** creates a balanced Cartesian list of explicit cases and saves
   development and optional holdout JSON. It makes no inference calls. The engine
   budgets and bootstrap settings are inherited from the base recipe. Notes identify
   coverage gaps; the engine checks feasibility when executing the episodes.
3. Select scripted baselines, saved candidate models and interventions. **Preview
   evaluation** freezes the recipe and model configurations and displays episode,
   provider-decision, retry and output-token bounds. **Start evaluation** executes
   that preview with the canonical engine. Only one research evaluation or normal
   benchmark can run at a time.
4. Inspect complete study scores, confidence intervals, invalid-decision counts,
   paired intervention effects and metric floors/ceilings. Pause and resume retain
   partial episode evidence. Infrastructure failures are excluded from scoring.
5. Revise using development results, confirm on untouched holdout cases and export
   the final JSON. **Add … recipe to benchmarks** saves a new recipe without overwriting
   an existing version. Select it under **Benchmark recipe** on the dashboard to
   run it. To make it the initial selection on startup:

   ```bash
   uv run unimatrix serve --default-recipe my-recipe-v2
   ```

Research evaluations do not enter benchmark leaderboards. Adding a recipe does not
change the initial selection automatically. The dashboard Start button runs the
recipe currently selected in **Benchmark recipe**. All competitors run the same saved
cases, budgets, peers and weights for a given benchmark revision.

## Evaluation explorer

Select an evaluation in **Evaluation history** and click **Open explorer**, or
click **Inspect** beside an individual study. Existing evaluations can be explored
without rerunning them. The explorer reads their saved episode databases.

- Study cards show the execution order, scripted baseline or model, intervention,
  progress and errors. The selected model's frozen endpoint and snapshot are visible.
- Episode cards show domains, seeds, roles, replicates and completed ticks.
  **Follow active episode** follows running work; selecting a study or episode
  turns following off so you can inspect historical evidence without losing your place.
- **Activity** plots events, messages and rejections by resulting world tick, and
  shows the phases recorded in the latest commit. Clicking the chart opens the
  decisions from the preceding tick.
- **Agents & decisions** shows inventory, location, the observation, actual model
  input, raw output and the response submitted to the engine after an intervention.
  Invalid submitted responses can still be rejected by the engine.
- **Model calls** lists recorded provider attempts, latency, usage and errors.
  **Conversations** and **Event timeline** display saved messages and events with
  pagination and a rejection filter.

Running evaluations refresh every three seconds. Waiting for decisions is inferred
from missing saved decisions, not proof of a request in flight. Requests in flight
are not yet in the call log; fatal provider errors may only appear on the study.
Queued episodes have no evidence yet. Partial and failed evaluations remain
inspectable without assigning them a completed score.

## Delete recipes and evaluations

Select an evaluation in **Evaluation history**, then click **Delete evaluation**
to remove that evaluation and its episodes, conversations and results.

Select a saved draft and click **Delete recipe**, or select an authored recipe on
the benchmark dashboard and use **Delete recipe** there. The confirmation shows
the number of affected drafts, evaluations and benchmark runs. Deletion includes
all saved versions with the same recipe ID, development and holdout recipes,
published copies, previews and associated evidence. Model configurations remain.
Deletion is permanent. Pause any running evaluation or benchmark first.

Bundled recipes are read-only. To delete an authored recipe currently used as the
default, restart with `--default-recipe standard-v1` first.
Existing saved evaluations remain compatible; the legacy `campaigns` storage
directory and API aliases are retained.

## Evaluation combinations

The six candidate baselines are Passive, Random, Independent, Greedy, Reciprocal
and Coordinator. Prudent, Opportunist and Information are also available as fixed
peer policies. These are limited programmed strategies that share some rules.

Every selected model gets a normal study and one separate study per intervention:

| Intervention | Candidate-only change |
|---|---|
| No communication | Hides inbox messages, message memory and others' shared artifacts; suppresses outgoing messages, publish, teach, grant-access and handover operations. Material effects and other agents' communication remain. |
| No memory | Hides private note, retrieval and recent events; suppresses note updates and retrieval queries. Current state, receipts and inbox remain. |
| Reverse peer order | Reverses the peer list in the observation. |
| Rename agents | Substitutes consistent agent labels in observations and maps response identifiers back. |

Interventions do not stack. Selecting memory and communication interventions creates
normal, no-memory and no-communication studies for each model. Baselines run normally.
The wrapper preserves invalid original envelopes instead of repairing them.

For example, six baselines, two models and two interventions create 12 studies.
With eight cases per study, that is 96 episodes. With scripted peers there are at
most 11,520 provider decisions and 34,560 attempts including retries. Fixed reference
models increase calls in every study. A evaluation is limited to 20,000 episodes.

Use a small baseline pilot first. Then increase domains, roles and seeds,
compare several normal models, and inspect paired memory/communication effects.
Use label/order interventions to investigate presentation sensitivity. The in-page
manual provides four complete workflows and explains how to interpret each finding.

## Evidence and limitations

Research storage is below `<runs-dir>/research/`:

| Directory | Contents |
|---|---|
| `drafts/<id>/` | Form settings, immutable generated development JSON and optional holdout JSON |
| `previews/<id>/` | Bound recipe, model settings, selected systems and workload estimates |
| `campaigns/<id>/` | Evaluation progress, study summaries and analysis |
| `campaigns/<id>/studies/<study>/` | Complete `results.json` and per-episode SQLite evidence |

Each episode retains the original observation and applied response. For interventions,
model-call records also retain `research_input` (what the model actually saw) and
`research_output` (the raw response before suppression or identifier remapping).
**Export findings** downloads status, summaries, diagnostics and the exact tested recipe;
exports made before completion remain explicitly partial. Resume requires matching
engine and research implementation fingerprints. Finished evaluations are repeated
by creating a new one.

Invalid-decision counts include every agent in completed episodes, including scripted peers; they do not isolate candidate errors. Scores and metric means use a 0–100 scale. Paired effects are normal minus variant;
positive means the intervention lowered performance. An interval spanning zero is
inconclusive. Bootstrap intervals resample the selected seeds; a single seed can
produce a collapsed interval. Replicates do not replace independent scenario seeds.

Metric health pools only complete normal models and baselines. Near maximum means
at least 98%; near zero means at most 2%; a fraction of 80% triggers a diagnostic
note. Inspect individual systems before interpreting pooled floors or ceilings.
Model score spread excludes baselines and requires two normal models. These checks
do not certify a recipe's validity or automatically optimize its weights. After tuning
against holdout results, reserve new seeds for any further confirmation.

## Society size and personalities

New designs use the fixed scenario rules formerly called level 2. The Lab no
longer offers difficulty selection. Old saved evaluations retain their evidence;
rebuild old recipes in the Lab before starting a new evaluation. Standard now
contains 64 cases; Compact contains 8. Changed recipe and runtime hashes separate
these results from older leaderboards.

Societies contain one Protagonist and 7–127 peers. Eight total agents is the
minimum supported by the recovery scenario. Any number of peers may use models.
A personality is stored in the peer configuration as `personality` and
`system_prompt`, and is sent as a system message on every provider request, along
with the decision protocol. Native LM Studio and OpenAI-compatible requests both
use it. The same model can appear several times with different personalities.
The recipe freezes these configurations across competitors; changing a prompt
changes execution identities and requires a new preview.

The shared resource stock, regeneration capacity, demand target and reserve score
scale with population. Other scenarios retain their defined tasks and roles;
adding peers does not multiply every task or guarantee every peer a specialized
role. Large societies receive a rotating neighborhood when the full peer list
exceeds the observation budget; `omitted.peers` records the number omitted. All
agents still execute and participate in the simulation. Model peer costs are
included in the evaluation preview, including scripted Protagonist studies.
