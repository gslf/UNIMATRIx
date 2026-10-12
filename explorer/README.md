# UNIMATRIx Explorer

A static, single-page explorer of society runs: one leaderboard per recipe, and for every run a
week-by-week replay of everything its steward did. It never talks to the UNIMATRIx application, its
API or its databases: it only reads [xCDN](https://github.com/gslf/xCDN) files.

## Open it

```bash
python3 -m http.server --directory explorer --bind 127.0.0.1 8090
```

Then open `http://127.0.0.1:8090`. The Explorer reads `data/index.xcdn` and fetches each run file
when you open it. Without a served `data/` folder (or when `index.html` is opened straight from disk,
where browsers block file reads) use **Open files**, or drop `.xcdn` files or whole folders onto the
page: an index with its `runs/`, or run files alone (a run file is enough to explore that run).
Nothing leaves the browser tab.

## Where the files come from

`data/` is git-ignored: the repository ships the software and the bundled recipes, never results.

- `uv run unimatrix demo --explorer-dir explorer` plays the scripted reference stewards (passive,
  naive, expert, oracle) of `standard-v2` and writes them to `data/` (`--recipe hell-v1` for the other).
- Model runs are exported to `data/` when they finish: `unimatrix serve` and `unimatrix run` take
  `--explorer-dir` (default `explorer`); `unimatrix export` exports runs that finished earlier.

Each export writes `data/runs/<run>.xcdn` and `data/entries/<run>.xcdn`, regenerates
`data/index.xcdn` (the concatenation of the entries) and refreshes `vendor/village.js`.

## What it shows

- **Leaderboard** (`#/`): one table per recipe and revision. Model runs are ranked by score (the
  mean over seeds; a collapsed seed scores zero), with × expert, seeds, WELLBY and the eight domains.
  Reference stewards are shown as marked, unranked milestone rows. "Latest per steward" keeps each
  steward's newest run; "All runs" lists every run. Click a row to explore the run.
- **Run explorer** (`#/run/<id>?seed=&week=`): the animated village (the steward walks to where it
  acts and speaks), transport controls and a scrubber marked with turning points, the score so far
  per domain, and a week inspector:
  - *Steward*: every action of the week with its status (applied, or rejected and why), whom it
    concerned, what the steward said, the outcome, the steward's notebook and forecasts, the chronicle;
  - *Mind*: the briefing the model read, its reasoning (when the provider exposes it) and its raw
    answer, with tokens, latency, finish reason, retries and parse errors;
  - *People*: every resident's state, and for each one wellbeing, trust, stress, health and coins over
    time, hidden concerns, fate, and every interaction with the steward;
  - *Story*: the story graph with the path this seed took, crises and outcomes.

  Below: a searchable quest log of every action of the seed, vital signs (population, wellbeing,
  trust, protest, commons, Gini, stores, points per domain, tokens and latency), and the final
  scoreboard with the collapse (if any), domain breakdown, narrative, outcomes and diagnosis.
  A seed selector appears when a run has several seeds. Keys: Space play/pause, ←/→ week,
  Home/End, `[` `]` speed, F full screen, 1–4 inspector tabs.

## File format

Both files are xCDN streams. Scores are exact decimals (`d"…"`), times `t"…"`, durations `r"…"`;
values are labelled with semantic tags. Fields that are empty are omitted.

`index.xcdn`

```
$schema: "https://gslf.github.io/xCDN/schemas/v1/meta.xcdn",
$format: "unimatrix.explorer.index/1",
#explorer { generated: t"…", runs: N, domains: { D1: { name, unit }, … } }
#entry { run, file: "runs/<run>.xcdn", kind: "model"|"reference", model, snapshot, recipe, recipe_name,
         revision, created: t"…", total: d"…", relative_to_expert: d"…", collapsed, seeds,
         collapse: #collapse { week, cause, label } | null, wellby: d"…", domains: { D1: d"…", … },
         vitals: { population, wellbeing, trust, commons, gini, deaths }, references: { passive, naive, expert, oracle } }
…one #entry per run
```

`runs/<run>.xcdn`

```
$schema: "…", $format: "unimatrix.explorer.run/1",
#run { id, kind, model, snapshot, model_id, status, created: t"…", finished: t"…", duration: r"…", usage: { calls, input_tokens, generated_tokens },
       recipe: { id, name, revision, description, weeks, seeds, actions_per_week, world, start_season, season_length,
                 commons: { name, capacity, critical }, collapse: { … } },
       score: { total: d"…", collapsed_seeds, relative_to_expert, wellby, domains: { D1: #domain { name, unit, points, units, rate }, … } },
       references: { … }, factions: [#faction { id, name, color }], residents: [#resident { … }],
       story: { nodes: [#node { id, title, kind, x, y, outcome, label }], edges: [[from, to, label]] },
       seeds: [#seed { seed, total, earned, forfeited, survived_weeks, collapsed, wellby, events, narrative,
                       diagnosis: [#finding { level, title, text, basis }], path: [#step { … }], outcomes: [#outcome { … }], actions }] }
#week { seed, week, season, metrics: { … }, points: { D1: d"…", … }, stores, commons: { stock, harvest }, skills,
        beliefs, conventions, projects, conflicts, story: [nodes activated this week],
        residents: @columns("id", "status", "activity", "wb", …) [[…], …], ties: [[a, b, strength]], events: [text],
        actions: [#action { verb, status, targets, place, say, text, label, params }],
        steward: { note, forecasts, error },
        decision: #decision { briefing, answer, reasoning, error, input_tokens, generated_tokens, reasoning_tokens,
                              finish_reason, latency: d"…", retries },
        collapsed: #collapse { … } }
…one #week per seed and week (week 0 is the initial state)
```

`decision` is present for model runs from week 1 on: `briefing` is the exact text the model read that
week, `answer` its raw reply, `reasoning` its chain of thought when the provider exposes it, `latency`
in seconds. Reference runs have no decisions. Entry files under `data/entries/` use
`$format: "unimatrix.explorer.entry/1"` with a single `#entry`.

## Code

- `index.html`, `explorer.css`: the page, in the identity of the UNIMATRIx dashboard; system fonts,
  no external resources.
- `data.js`: reads the files with the official xCDN JavaScript library, bundled as a classic script in
  `vendor/xcdn.js` (MIT, see `vendor/xcdn-LICENSE`; regenerate with `tools/bundle_xcdn.py`), and converts
  them to plain objects (integers and decimals become numbers, tags and `@columns` tables are resolved).
- `ui.js`: shared components; `explorer.js`: routing, file loading and the leaderboard;
  `replay.js`: the run explorer, built on the village renderer `vendor/village.js` (copied from the
  application by the exporter).
