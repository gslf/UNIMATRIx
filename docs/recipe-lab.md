# Society Lab

Open `/recipe-lab` to shape a society and the game a steward must solve in it. The Lab works on
desktop, tablet and phone (touch: drag, pan and pinch on the network and the choice graph).

A recipe is one JSON document (format `unimatrix.polis-recipe.v2`, see `src/unimatrix/polis/recipe.py`
for every field, default and validation rule). The bundled recipes `standard-v2` and `hell-v1` are
read-only: duplicate one to edit it. Saved recipes live in `config/societies/` (git-ignored) and
appear in the dashboard's recipe selector.

## Workflow

1. **Design** the society:
   - **People** — residents with role, faction, archetype, Big Five and behavioural traits, starting
     state, a hidden concern, skills, public flags, secrets and health decline; the social network
     of ties and factions.
   - **Behaviour archetypes** — parameter profiles grounded in sociology and game theory
     (conditional cooperators, free riders, strong reciprocators, gossips, elder keepers,
     vulnerable residents, agitators, patricians, newcomers, steady workers); edit them or add
     your own.
   - **World** — treasury, granary, prices, seasonal production, the commons (capacity, stock,
     growth, critical stock, hyperstability floor), default policies, conventions and illness.
   - **Merchants** — fair traders, sharks with counterfeit goods, guilds whose rules can change.
   - **Projects** — public works with skill needs, complementarity, costs, dependencies and
     benefits.
   - **Facts and rumours** — hidden truths, their sources (honest, mistaken or lying), how
     belief changes behaviour, inquiry accuracy and forecast probes.
   - **Choice graph** — situations triggered by time, chance or the state of the society, with
     effects and conditional branches that lead to crises, recoveries, collapse or flourishing.
2. **Set the stakes** — which collapse rules apply and their thresholds; the exchange rates that
   turn each domain's natural units into points.
3. **Preview** — play the society animated with a reference steward (passive, random, naive,
   expert, oracle) and watch the village, the charts, the events and the path through the
   choice graph.
4. **Calibrate** — score every reference steward on several seeds. A good benchmark makes the
   passive steward collapse, lets weak-but-attentive play survive (for an entry recipe) and leaves
   room above the expert. "Calibrate rates" sets exchange rates so the expert earns a chosen
   number of points per domain.
5. **Save** and run models from the dashboard.

Changing a saved recipe changes its hash: runs and leaderboard entries are kept per revision.
See [society.md](society.md) for the mechanics, scoring and literature behind each rule.
