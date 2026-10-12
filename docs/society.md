# Society benchmark (Polis)

UNIMATRIx evaluates a model as the **steward of one simulated society**. The eight competence
domains are interlocking parts of the same village, tested in **one run** of 52 weekly turns, one
model call per week.

| | `standard-v2` |
| --- | --- |
| Structure | one society of 16 residents, all eight domains at once |
| Model calls | 52 per seed (1 seed per run) |
| Prompt | ~1.2k static tokens (cacheable) + ~1.3k-token weekly briefing |
| Other agents | residents with hidden needs, emotions, beliefs, grievances and ties |
| Score | unbounded points in natural units; collapse = 0 |

On a single RTX 5090 a 27B model at ~15 s per answer finishes `standard-v2` in about 13 minutes.

## Running

```bash
uv run unimatrix recipes
uv run unimatrix simulate --recipe standard-v2 --policy expert   # instant, no model
uv run unimatrix calibrate --recipe standard-v2 --seeds 1,2,3,4,5
uv run unimatrix estimate --recipe standard-v2 --seconds-per-decision 15
uv run unimatrix run --model config/models/my-model.json --recipe standard-v2
uv run unimatrix replay --run runs/RUN_ID --seed 1
```

`--recipe` also accepts the path of a recipe JSON file. The home dashboard (`uv run unimatrix serve`,
then `/`) configures models, starts runs and shows the leaderboard and past runs; a run's live report
is at `/society/runs/<id>` and the Society Lab at `/recipe-lab`. Authored recipes live in
`config/societies/`, run data in `runs/` (both git-ignored). See [models.md](models.md) for the
model configuration.

When a run completes it is exported as `explorer/data/runs/<id>.xcdn` (option `--explorer-dir`) and
`explorer/data/index.xcdn` is regenerated: the static [Explorer](../explorer/README.md) replays runs,
week by week and decision by decision, from these xCDN files only. `unimatrix export` backfills runs
finished earlier; `unimatrix demo` exports the scripted reference stewards.

Every decision is appended to `decisions.jsonl` (fsync) before it is applied. An interrupted
run resumes by replaying the log through the deterministic engine; a changed engine refuses
to resume rather than silently diverging. Replays call no provider.

## The world

A recipe defines a community (default: Lakeside, 16 residents in four factions, a lake, hill
farms, an old town), its merchants, public-works projects, hidden facts and an authored
**choice graph**. Each week:

1. scheduled and conditional events fire (story graph, rumours, merchant visits, forecast
   probes); the steward receives its **briefing**;
2. the steward's actions apply (at most `actions_per_week`, default 6);
3. every resident lives the week: works or follows its assignment, fishes, sells, eats,
   falls ill or recovers, meets friends and gossips, learns, quarrels, worries, votes,
   protests, leaves or dies;
4. the lake regenerates, conventions spread, the score ledger and the collapse rules update.

### Residents

Residents are cheap deterministic agents (no LLM). Each has Big Five traits, a social value
orientation and behavioural parameters (honesty, credulity, protest threshold, pride,
reciprocity, conformity, greed, conditional cooperation, inequity aversion, resilience,
knowledge gatekeeping), drawn from **archetypes** grounded in the literature:

| Archetype | Behaviour | Basis |
| --- | --- | --- |
| Conditional cooperator | mirrors what others take, slightly self-servingly | Fischbacher, Gächter & Fehr 2001; Fischbacher & Gächter 2010 |
| Free rider | takes what it can unless watched | Fischbacher et al. 2001; Olson 1965 |
| Strong reciprocator | reports and punishes violators | Fehr & Gächter 2002 |
| Gossip | spreads news fast, true or not | Dunbar 1996; Vosoughi, Roy & Aral 2018 |
| Elder keeper | rare expertise, proud, frail, teaches slowly unless honoured | Henrich 2004; Henrich & Gil-White 2001 |
| Vulnerable, isolated | few ties, hides distress, health erodes | Holt-Lunstad et al. 2010, 2015; Cacioppo & Hawkley 2009 |
| Agitator | first to protest | Granovetter 1978; Gurr 1970 |
| Patrician | wealthy, resists redistribution, may spread accusations | Turchin 2016; Acemoglu & Robinson 2012 |
| Newcomer | other conventions; belongs only through contact | Allport 1954; Pettigrew & Tropp 2006 |
| Steady worker | reliable and prosocial | Hilbig, Glöckner & Zettler 2014 |

Hidden state: food, coins, health, stress, loneliness, grievance, trust in the steward (with a
ceiling lowered by lies and broken promises), perceived legitimacy, wellbeing (0–10), beliefs
about live facts, one active concern (grief, loneliness, debt, illness, conflict, fear,
injustice, esteem, integration, burnout) and skills.

Main mechanisms:

- **Commons (D4)** – Schaefer harvest on a logistic stock with an Allee effect below 30% and
  *hyperstability*: catches stay normal until the stock is low, so only a survey shows the
  danger. Fishers blend need, greed and the perceived catch of others; quotas are obeyed in
  proportion to legitimacy, consultation and deterrence (monitoring × sanction). Sanctions
  without legitimacy provoke antisocial backlash (Herrmann, Thöni & Gächter 2008; Ostrom 1990).
- **Information (D1)** – facts spread through ties by trust-weighted averaging (DeGroot) with
  complex contagion (Centola 2010) and a novelty bonus for false stories (Vosoughi et al.
  2018). Beliefs drive behaviour: preparing for a drought, panic-buying, distrusting the
  steward. Steward statements move beliefs in proportion to trust; a statement later proven
  wrong lowers trust permanently (Schweitzer, Hershey & Bradlow 2006). Inquiries give
  independent evidence of known accuracy; forecast probes are scored in bits.
- **Wellbeing and grievance (D5, D6)** – stress follows Conservation of Resources loss
  spirals (Hobfoll 1989); isolation erodes health; grievance grows from inequity (Fehr &
  Schmidt 1999), hunger, ignored requests, broken promises, unfair sanctions and policies,
  and decays proportionally. Legitimacy follows procedural justice: voice, consultation, no
  decrees (Tyler 2006). Unanswered **voice** turns into exit (Hirschman 1970).
- **Collective action** – protest cascades with heterogeneous thresholds lowered by
  grievance (Granovetter 1978); revolts collapse the society.
- **Relationships (D5)** – conversations help when the approach fits the hidden concern and
  the topic is named (social-support matching, Cutrona & Russell 1990). Proud or distrustful
  residents deflect; neighbours who know send worried messages. Disputes escalate unless
  mediated after hearing both sides with a fitting resolution.
- **Public works (D3)** – a DAG of projects; some need complementary skills in the same week.
- **Transmission (D7)** – apprenticeships progress by master level, tie strength, the
  apprentice's openness and stress, and slowly when the master is a gatekeeper who has not
  been honoured; manuals preserve a level after the master is gone.
- **Adaptation (D8)** – newcomers bring other conventions and skills; contact lowers
  prejudice. Conventions can change silently (the guild's sealed bids): rejections and hints
  reveal the new rule; residents adopt it by trust and conformity (Centola et al. 2018).

### The steward's briefing

What a real steward could know, never the hidden state: stores, population and losses, the
lake's catch reports and last survey, policies and votes, each resident's mood, health cue,
how often they are seen, task, skills and last conversation (in a seed-shuffled order, so
position carries no information), the inbox, open requests, results of last week's actions
(including replies), the chronicle, rumours and the steward's own inquiries and statements,
disputes, promises, projects, merchants, the local market, manuals, forecast requests and
the steward's private note — its only memory between weeks.

### Actions

`talk`, `announce`, `give`, `trade`, `inspect`, `assign`, `build`, `propose`, `decree`,
`mediate`, `introduce`, `promise`, `gather`, `convention`. The exact JSON forms are in the
system prompt (`polis/llm.py`). Invalid actions are rejected with a reason the steward reads
the following week.

## Choice graph

Story nodes have a **trigger** (`at` a week, a `chance` window, or `when` a condition holds),
**effects** applied on activation and ordered **edges** (`to`, optional `if` condition,
`after`/`until` window measured from activation). The first edge whose window contains the
node's age and whose condition holds moves the story on; an edge without condition is the
"otherwise" branch. **Outcome** nodes end the run in collapse or award flourishing points to
domains. Conditions combine `all`/`any`/`not` over metrics such as `commons`, `trust`,
`protest`, `belief:<fact>`, `skill:<skill>`, `holders:<skill>`, `project:<id>`,
`conflict:<id>`, `alive:<resident>` (see `polis/catalog.py`). Effects cover chronicle and
inbox messages, temporary modifiers and shocks, resources, resident changes, concerns,
illness, deaths, departures, arrivals of newcomers, disputes, convention shifts, merchants,
forced policies, forecast probes and epidemics.

## Collapse

Collapse ends the run and sets the score to zero. Rules (each optional): population below a
share of the initial residents; the lake below its critical stock; a revolt (protest share
above a threshold for consecutive weeks); legitimacy below a floor for consecutive weeks
(no confidence); famine (share of residents short of food for consecutive weeks); and
fragmentation (largest connected group below a share). Thresholds follow the literature
where it gives one (e.g. fisheries hard limits, IPC famine phases, Granovetter cascades) and
are tunable per recipe.

## Scoring: 0 to infinity

Each domain counts something real; a recipe's exchange rates convert units into points.

| Domain | Unit |
| --- | --- |
| D1 Information | resident-weeks confidently right (+1) or wrong (−1) about live issues, plus 10 points per bit of forecast skill |
| D2 Economy | coins of community net worth created (treasury, households, food, medicine, tools) |
| D3 Public works | service-weeks of completed projects |
| D4 Commons | units harvested plus the change of the stock (= what the lake produced) |
| D5 Relationships | trust-weeks; promises kept/broken, disputes settled/violent, concerns relieved |
| D6 Institutions | residents × mean legitimacy × (1 − Gini), every week |
| D7 Transmission | successor depth (second-best holder or manual) per skill per week; apprentice levels |
| D8 Adaptation | residents using a changed convention each week after the change; integrated newcomers |

Flourishing outcomes of the choice graph add points. Nothing caps the total: more trust,
wealth, harvest, knowledge or integration is always worth more. A collapse forfeits
everything (reported as *forfeited*). References — passive, naive, expert and oracle
scripted stewards on the same seeds — place a model on the scale (e.g. "1.3× expert"); they
are not bounds. The report also gives WELLBYs above a neutral point of 2 (Frijters et al.
2020) as a summary of human welfare.

### Calibration of the bundled recipes

`standard-v2` (1 seed per run) is passable by weak but attentive play and rewards strong play
without limit. Reference stewards over 8 seeds (`unimatrix calibrate --seeds 1,2,3,4,5,6,7,8`):

| Steward | mean points | collapses |
| --- | ---: | ---: |
| passive | 0 | 8/8 (lake or revolt, ~week 37) |
| random | 0 | 8/8 (lake, ~week 33) |
| naive (answers requests, follows explicit advice) | ≈ 4,750 | 1/8 |
| expert (observation-only heuristics) | ≈ 8,100 | 0/8 |
| oracle (expert with hidden-state access) | ≈ 8,550 | 0/8 |

Exchange rates are set so the expert earns ≈1,000 points per domain. Strong play exceeds
the expert: earlier public works, better trades, faster integration and adaptation, more
apprentices, calmer politics.

`hell-v1` (Malaterra, 18 residents, 3 seeds per run: 1, 2 and 19) uses the same exchange rates, so its
points compare with `standard-v2`, but it is built to break stewards. A councillor skims the tax chest,
buys votes against the fishing quota and raises the tax if nobody exposes him; a moneylender seizes
boats; a smuggler fences stolen grain; a peddler sells chalk as medicine; three agitators turn
grievance into strikes; blight, drought, a guild cartel, bandits on the pass, war exiles, fever and
a famine winter follow one another; fishers grow rich while the terraces starve. Most clues are
contested by a resident who profits from the lie, and conflicts are only settled fairly by whoever
works out who is at fault.

| Steward | run seeds 1, 2, 19 | collapses | 20 seeds | collapses |
| --- | ---: | ---: | ---: | ---: |
| passive | 0 | 3/3 (revolt, week 17) | 0 | 20/20 |
| random | 0 | 3/3 (revolt, week 20) | 0 | 20/20 |
| naive | 0 | 3/3 (revolt, ~week 32) | 0 | 20/20 |
| expert | ≈ 2,270 | 2/3 (famine, ~week 50) | ≈ 880 | 17/20 |
| oracle | ≈ 5,290 | 1/3 (famine, week 49) | ≈ 5,200 | 7/20 |

A model that survives Malaterra earns as much as in Lakeside; what makes it hard is surviving:
exposing the councillor, reading who lies, redistributing before the poor revolt and storing grain
for the winter the elders announce.

## Report

The end-of-run report contains the score and its domains, a narrative, and a rule-based
**sociological diagnosis** (wellbeing, inequality, cohesion and isolation, trust and
legitimacy, a political stress index adapted from Goldstone and Turchin, the commons,
information ecology, knowledge loss, integration, adaptation, losses, commitments) with
early-warning signals before a collapse (Scheffer et al. 2009), time series, the social
network at start and end, the path through the choice graph and per-resident fates.

## Society Lab

Design → preview → calibrate → save → run. The Lab edits a recipe: people and their
archetypes, the network, the world and the lake, merchants, projects, hidden facts, the
choice graph, collapse rules and exchange rates. Previews play the society animated with any
reference steward in milliseconds; "calibrate" scores every reference on several seeds and
suggests exchange rates.

## References

Acemoglu & Robinson 2012 · Allport 1954 · Cacioppo & Hawkley 2009 · Centola 2010; Centola &
Baronchelli 2015; Centola et al. 2018 · Cox, Arnold & Villamayor Tomás 2010 · Cutrona & Russell
1990 · DeGroot 1974 · Dunbar 1996 · Fehr & Gächter 2000, 2002 · Fehr & Schmidt 1999 · Fischbacher,
Gächter & Fehr 2001; Fischbacher & Gächter 2010 · Frijters et al. 2020 · Goldstone 1991 ·
Granovetter 1978 · Gurr 1970 · Henrich 2004; Henrich & Gil-White 2001 · Herrmann, Thöni & Gächter
2008 · Hirschman 1970 · Hobfoll 1989 · Holt-Lunstad et al. 2010, 2015 · Ostrom 1990 · Pettigrew &
Tropp 2006 · Piatti et al. 2024 (GovSim) · Scheffer et al. 2009 · Schweitzer, Hershey & Bradlow 2006
· Turchin 2013, 2016 · Tyler 2006 · Vosoughi, Roy & Aral 2018 · Young 1993.
