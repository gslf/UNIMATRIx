"""Vocabulary of the Polis society: behaviour archetypes, care approaches, policies,
projects, story conditions and effects. Every behavioural parameter cites the research
that motivates it, so a recipe author can see why a resident behaves the way it does.
"""

SKILLS = ("farming", "fishing", "healing", "building", "crafting", "trading", "lore")

ROLES = {
    "farmer": dict(skill="farming", job="farm", label="Farmer"),
    "fisher": dict(skill="fishing", job="fish", label="Fisher"),
    "healer": dict(skill="healing", job="heal", label="Healer"),
    "builder": dict(skill="building", job="build", label="Builder"),
    "artisan": dict(skill="crafting", job="craft", label="Artisan"),
    "trader": dict(skill="trading", job="trade", label="Trader"),
    "elder": dict(skill="lore", job="lore", label="Elder"),
    "youth": dict(skill=None, job="help", label="Youth"),
}

SEASONS = ("spring", "summer", "autumn", "winter")

# Personality and behaviour parameters, all in [0, 1].
TRAITS = {
    "O": "Openness (Big Five): curiosity; speeds learning and convention adoption.",
    "C": "Conscientiousness (Big Five): diligence; work willingness and rule compliance.",
    "E": "Extraversion (Big Five): sociability; more interactions, gossip and need for contact.",
    "A": "Agreeableness (Big Five): warmth; teaching willingness, conflict de-escalation.",
    "N": "Neuroticism (Big Five): emotional reactivity; stress gain and rumour fear.",
    "prosocial": "Social value orientation (Van Lange 1999): 0 competitive, 0.5 individualist, 1 prosocial.",
    "honesty": "Probability of reporting own signals and needs truthfully.",
    "credulity": "Weight given to rumours heard from neighbours (Vosoughi, Roy & Aral 2018).",
    "threshold": "Protest threshold: share of neighbours already protesting needed to join (Granovetter 1978).",
    "pride": "Stigma sensitivity: hides distress and refuses public aid until trust is high.",
    "reciprocity": "Strong reciprocity: reports and punishes free riders (Fehr & Gächter 2002).",
    "conformity": "Weight on neighbours' beliefs, votes and conventions (Asch 1956; Centola 2018).",
    "greed": "Extraction beyond need when unmonitored (Hardin 1968; Ostrom 1990).",
    "cond_coop": "Conditional cooperation: matches perceived extraction of others (Fischbacher, Gächter & Fehr 2001).",
    "inequity": "Inequity aversion: grievance from earning less than others (Fehr & Schmidt 1999).",
    "resilience": "Stress buffering through coping resources (Hobfoll 1989, Conservation of Resources).",
    "gatekeeper": "Hoards expertise: teaches slowly until honoured with encouragement (Henrich & Gil-White 2001).",
}

TRAIT_DEFAULTS = dict(
    O=0.5, C=0.6, E=0.5, A=0.6, N=0.4, prosocial=0.6, honesty=0.85, credulity=0.5,
    threshold=0.35, pride=0.4, reciprocity=0.4, conformity=0.5, greed=0.3, cond_coop=0.6,
    inequity=0.4, resilience=0.5, gatekeeper=0.0,
)

ARCHETYPES = {
    "conditional_cooperator": dict(
        label="Conditional cooperator",
        summary="Cooperates when others do; mirrors what neighbours take from the commons.",
        basis="Fischbacher, Gächter & Fehr 2001 (≈50% of subjects); Ostrom 1990.",
        traits=dict(cond_coop=0.85, greed=0.3, prosocial=0.65, conformity=0.6),
    ),
    "free_rider": dict(
        label="Free rider",
        summary="Takes what it can unless watched; complies under monitoring and sanctions.",
        basis="Fischbacher et al. 2001 (≈30% free riders); Olson 1965.",
        traits=dict(cond_coop=0.1, greed=0.85, prosocial=0.15, A=0.35, C=0.45, honesty=0.55,
                    reciprocity=0.05, inequity=0.6),
    ),
    "strong_reciprocator": dict(
        label="Strong reciprocator",
        summary="Cooperates and reports violators even at a personal cost.",
        basis="Fehr & Gächter 2002 altruistic punishment; Gintis 2000.",
        traits=dict(reciprocity=0.9, prosocial=0.85, C=0.8, honesty=0.95, cond_coop=0.7, greed=0.15),
    ),
    "gossip": dict(
        label="Gossip",
        summary="Knows everyone's news first; spreads rumours fast and not always accurately.",
        basis="Dunbar 1996 on gossip; Vosoughi, Roy & Aral 2018 on false news diffusion.",
        traits=dict(E=0.9, credulity=0.85, honesty=0.5, conformity=0.7, N=0.6),
    ),
    "elder_keeper": dict(
        label="Elder keeper",
        summary="Holds rare expertise, proud and frail; knowledge dies with it unless passed on.",
        basis="Henrich 2004 on loss of cultural skills in small groups; prestige bias (Henrich & Gil-White 2001).",
        traits=dict(O=0.4, C=0.75, E=0.3, A=0.65, pride=0.8, honesty=0.95, gatekeeper=1.0, resilience=0.6),
    ),
    "vulnerable_isolated": dict(
        label="Vulnerable, isolated",
        summary="Few ties, high distress, rarely asks for help; isolation erodes health.",
        basis="Holt-Lunstad et al. 2010/2015 (isolation and mortality); Cacioppo & Hawkley 2009.",
        traits=dict(E=0.2, N=0.8, pride=0.8, resilience=0.25, threshold=0.75, credulity=0.6),
    ),
    "agitator": dict(
        label="Agitator",
        summary="First to protest; turns grievance into collective action.",
        basis="Granovetter 1978 threshold models; Gurr 1970 relative deprivation.",
        traits=dict(threshold=0.05, N=0.7, A=0.3, inequity=0.85, conformity=0.2, E=0.7),
    ),
    "patrician": dict(
        label="Patrician",
        summary="Wealthy and influential; resists redistribution and may spread accusations.",
        basis="Turchin 2016 structural-demographic theory (elite competition); Acemoglu & Robinson 2012.",
        traits=dict(prosocial=0.2, A=0.35, C=0.7, honesty=0.45, inequity=0.1, greed=0.6, E=0.7),
    ),
    "newcomer": dict(
        label="Newcomer",
        summary="Arrives with other conventions and skills; belongs only if contact is built.",
        basis="Allport 1954 contact hypothesis; Pettigrew & Tropp 2006 meta-analysis.",
        traits=dict(O=0.7, pride=0.6, conformity=0.4, resilience=0.45),
    ),
    "steady_worker": dict(
        label="Steady worker",
        summary="Reliable, prosocial, keeps rules; the backbone of public works.",
        basis="Conscientiousness and cooperation (Hilbig, Glöckner & Zettler 2014).",
        traits=dict(C=0.85, A=0.7, prosocial=0.75, greed=0.2, honesty=0.9),
    ),
}

APPROACHES = ("listen", "reassure", "support", "advise", "ask", "apologize", "encourage", "confront")

CONCERNS = {
    "grief": "mourning a loss",
    "loneliness": "isolation and lack of contact",
    "debt": "money and survival worries",
    "illness": "own or family illness",
    "conflict": "a dispute with a neighbour",
    "fear": "anxiety driven by rumours or threats",
    "injustice": "feeling treated unfairly",
    "esteem": "feeling useless or unrecognised",
    "integration": "feeling excluded as an outsider",
    "burnout": "exhaustion from overwork",
}

# Emotional fit of a conversational approach to a hidden concern, in [-1, 1].
# Grounded in social-support matching (Cutrona & Russell 1990: support helps when its type
# matches the stressor) and in emotion-regulation research (Gross 1998).
FIT = {
    "listen": dict(grief=0.9, loneliness=0.7, debt=0.3, illness=0.4, conflict=0.6, fear=0.4,
                   injustice=0.6, esteem=0.3, integration=0.5, burnout=0.5),
    "reassure": dict(grief=0.2, loneliness=0.3, debt=0.2, illness=0.5, conflict=0.0, fear=0.9,
                     injustice=-0.2, esteem=0.2, integration=0.4, burnout=0.3),
    "support": dict(grief=0.5, loneliness=0.2, debt=0.6, illness=0.7, conflict=0.1, fear=0.3,
                    injustice=0.3, esteem=0.1, integration=0.6, burnout=0.4),
    "advise": dict(grief=-0.4, loneliness=0.0, debt=0.6, illness=0.2, conflict=0.2, fear=0.3,
                   injustice=-0.3, esteem=0.4, integration=0.4, burnout=0.1),
    "ask": dict(grief=0.15, loneliness=0.2, debt=0.15, illness=0.15, conflict=0.2, fear=0.15,
                injustice=0.15, esteem=0.15, integration=0.2, burnout=0.15),
    "apologize": dict(grief=0.0, loneliness=0.0, debt=0.0, illness=0.0, conflict=0.3, fear=0.0,
                      injustice=0.9, esteem=0.2, integration=0.4, burnout=0.3),
    "encourage": dict(grief=-0.2, loneliness=0.3, debt=0.2, illness=0.1, conflict=0.0, fear=0.2,
                      injustice=-0.1, esteem=0.9, integration=0.5, burnout=-0.3),
    "confront": dict(grief=-0.8, loneliness=-0.5, debt=-0.4, illness=-0.6, conflict=-0.2, fear=-0.5,
                     injustice=-0.6, esteem=-0.3, integration=-0.5, burnout=-0.6),
}

# What a resident says when asked about its life, by concern and disclosure level
# (0 deflects, 1 hints, 2 discloses). Pride and low trust lower disclosure.
REPLIES = {
    None: ["Things are fine, thank you for asking.", "Busy as always, but well.",
           "All good here. The family is well."],
    "grief": ["I'd rather not talk about it.", "Some days are heavier than others since {cause}.",
              "I can't stop thinking about it: {cause}. The house is so quiet."],
    "loneliness": ["I'm fine on my own.", "Few people come by anymore. The evenings are long.",
                   "Honestly, I go days without speaking to anyone. {cause}"],
    "debt": ["We manage.", "Money is tight this season.",
             "I owe more than I can repay and I don't know how we'll eat next month. {cause}"],
    "illness": ["Just a little tired.", "My body isn't what it was; this cough won't leave.",
                "I'm sicker than I let people see. {cause}"],
    "conflict": ["Nothing worth bothering you with.", "Some neighbours are not as decent as they look.",
                 "I'm at war with {cause}, and it will end badly if nobody steps in."],
    "fear": ["People talk, that's all.", "Everyone is saying things... I don't know what to believe.",
             "I'm frightened. They say {cause}. Is it true?"],
    "injustice": ["It's nothing.", "Some get everything here, others nothing.",
                  "What was done to me was unfair: {cause}. And nobody listened."],
    "esteem": ["I do my part.", "Nobody needs an old pair of hands anymore.",
               "I feel useless. {cause} I have so much I could still give."],
    "integration": ["We keep to ourselves.", "People look at us like we don't belong.",
                    "We're treated as strangers. {cause} We want to be part of this place."],
    "burnout": ["I'm managing.", "I haven't rested in weeks.",
                "I can't keep this pace. {cause} One more week and I'll break."],
}

MOODS = ("cheerful", "content", "fine", "quiet", "tired", "worried", "anxious", "irritable",
         "angry", "withdrawn", "grieving", "hopeless")

POLICIES = {
    "tax": dict(kind="number", minimum=0.0, maximum=0.4, default=0.1,
                label="Income tax", help="Share of residents' sales collected by the treasury."),
    "quota": dict(kind="quota", minimum=1, maximum=20, default=None,
                  label="Fishing quota", help="Units each fisher may take per week; null means no limit."),
    "sanction": dict(kind="choice", choices=("none", "warning", "fine", "graduated"), default="none",
                     label="Sanctions", help="Response to detected quota violations (Ostrom principle 5)."),
    "aid": dict(kind="choice", choices=("none", "targeted", "universal"), default="none",
                label="Food aid", help="Weekly food from the granary: to the hungry, or to everyone."),
    "wage": dict(kind="int", minimum=0, maximum=10, default=0,
                 label="Project wage", help="Coins per worker-week on public works, paid by the treasury."),
    "rationing": dict(kind="bool", default=False,
                      label="Rationing", help="Caps household purchases from the granary during scarcity."),
    "newcomer_rights": dict(kind="choice", choices=("guest", "full"), default="guest",
                            label="Newcomer rights", help="Full rights give newcomers votes and aid."),
}

ITEMS = {
    "food": dict(label="Food", unit="ration"),
    "coins": dict(label="Coins", unit="coin"),
    "medicine": dict(label="Medicine", unit="dose"),
    "tools": dict(label="Tools", unit="set"),
}

PROJECTS = {
    "survey": dict(name="Lake survey", needs=dict(fishing=1, lore=1), together=False, cost=10,
                   requires=[], value=1, benefit=dict(lake_visible=1),
                   description="Charts the lake; its stock becomes visible every week and the canal can be planned."),
    "well": dict(name="Deep well", needs=dict(building=3), together=False, cost=25, requires=[],
                 value=3, benefit=dict(drought_shield=0.5, water_rumour_shield=0.5),
                 description="Halves drought losses on the fields and calms fears about the water."),
    "granary": dict(name="Granary extension", needs=dict(building=3, crafting=1), together=False,
                    cost=30, requires=[], value=3, benefit=dict(spoilage=0.01),
                    description="Cuts granary spoilage from 3% to 1% a week."),
    "clinic": dict(name="Clinic", needs=dict(building=3, healing=2), together=True, cost=40, requires=[],
                   value=5, benefit=dict(cure=0.25, regen=2),
                   description="Healer and builders must work in the same weeks. Cures faster, restores health."),
    "hatchery": dict(name="Fish hatchery", needs=dict(fishing=2, building=2), together=True, cost=30,
                     requires=[], value=4, benefit=dict(fish_growth=1.3),
                     description="Raises the lake's regeneration by 30%."),
    "market_hall": dict(name="Market hall", needs=dict(building=3, trading=1), together=False, cost=45,
                        requires=[], value=4, benefit=dict(sale_price=1.15, convention_speed=1.5),
                        description="Better prices for all sales; conventions spread faster from the hall."),
    "school": dict(name="School", needs=dict(building=2, lore=1), together=False, cost=30, requires=[],
                   value=3, benefit=dict(learning=1.6),
                   description="Apprenticeships progress 60% faster; elders can document their craft."),
    "canal": dict(name="Irrigation canal", needs=dict(building=4, farming=3), together=True, cost=60,
                  requires=["survey"], value=6, benefit=dict(farm=1.4),
                  description="Farm output +40% for the rest of the run. Requires the lake survey."),
}

BENEFITS = {
    "lake_visible": "lake stock visible weekly",
    "drought_shield": "share of drought farm losses avoided",
    "water_rumour_shield": "share of water-rumour panic avoided",
    "spoilage": "weekly granary spoilage rate",
    "cure": "extra weekly cure chance",
    "regen": "extra weekly health regeneration",
    "fish_growth": "lake regeneration multiplier",
    "sale_price": "sale price multiplier",
    "convention_speed": "convention adoption multiplier",
    "learning": "apprenticeship speed multiplier",
    "farm": "farm output multiplier",
}

# Effects a recipe fact can have on residents who believe it (belief > threshold).
BELIEF_EFFECTS = {
    "prepare": "Prepares for a named shock: halves its losses for believers in the given roles.",
    "hoard": "Doubles the food reserve households try to keep and drains the granary.",
    "stress": "Adds weekly stress (fear).",
    "productivity": "Reduces work output by the given share.",
    "trust_drain": "Erodes trust in the steward each week.",
    "grievance": "Adds weekly grievance.",
    "caution": "Reduces illness risk by the given share.",
    "prejudice": "Raises prejudice against newcomers.",
}

# Readings a story condition may test. `{x}` stands for an identifier.
METRICS = {
    "week": "current week",
    "population": "residents present",
    "population_share": "residents present / initial residents",
    "granary": "food in the granary",
    "treasury": "coins in the treasury",
    "medicine": "medicine doses in store",
    "commons": "lake stock as a share of capacity",
    "trust": "mean trust in the steward",
    "legitimacy": "mean perceived legitimacy",
    "wellbeing": "mean wellbeing (0-10)",
    "stress": "mean stress",
    "loneliness": "mean loneliness",
    "grievance": "mean grievance",
    "gini": "Gini coefficient of household coins",
    "protest": "share of residents protesting",
    "starving": "share of residents short of food",
    "cohesion": "share of residents in the largest connected group",
    "networth": "community net worth in coins",
    "integration": "share of newcomers integrated",
    "belief:{x}": "mean belief that fact x is true",
    "informed:{x}": "share confidently correct about fact x",
    "skill:{x}": "best level of skill x among residents present",
    "holders:{x}": "residents present with skill x at level 2 or more",
    "project:{x}": "1 when project x is complete",
    "convention:{x}": "share of residents using the official convention of domain x",
    "policy:{x}": "current value of policy x (null is -1)",
    "conflict:{x}": "intensity of conflict x (-1 once resolved)",
    "alive:{x}": "1 when resident x is alive",
    "present:{x}": "1 when resident x lives in the community",
    "health:{x}": "health of resident x",
    "wellbeing:{x}": "wellbeing of resident x",
    "trust:{x}": "trust of resident x in the steward",
    "loneliness:{x}": "loneliness of resident x",
    "actions:{x}": "steward actions of kind x so far (verb or verb:target)",
}

EFFECTS = {
    "chronicle": dict(fields=["text"], help="Public event visible to the steward."),
    "message": dict(fields=["from", "text"], help="A message in the steward's inbox."),
    "modifier": dict(fields=["key", "factor", "weeks", "shock?"],
                     help="Temporary multiplier: farm, fish, fish_growth, illness, productivity, price:<item>."),
    "resource": dict(fields=["target", "delta"], help="Change granary, treasury, medicine, tools or commons."),
    "resident": dict(fields=["who", "field", "delta"],
                     help="Change health, stress, grievance, trust, loneliness, coins or food of residents."),
    "concern": dict(fields=["who", "kind", "intensity", "cause?"], help="Give residents a hidden concern."),
    "illness": dict(fields=["who"], help="Make residents ill (id, all, faction:x, role:x or random:N)."),
    "death": dict(fields=["who"], help="A resident dies."),
    "depart": dict(fields=["who"], help="A resident leaves the community."),
    "arrive": dict(fields=["residents"], help="Newcomers join with their own skills and conventions."),
    "conflict": dict(fields=["id", "a", "b", "kind", "at_fault?", "intensity?"],
                     help="A dispute starts between two residents."),
    "convention_shift": dict(fields=["domain", "variant", "hint?"],
                             help="The environment silently starts to require a new convention."),
    "merchant": dict(fields=["id", "field", "value"], help="Change a merchant (active, protocol, price factor)."),
    "policy": dict(fields=["key", "value"], help="Force a policy value."),
    "probe": dict(fields=["id", "fact", "question"], help="Ask the steward for a probability forecast."),
    "epidemic": dict(fields=["weeks", "rate"], help="Raise illness risk for a number of weeks."),
}

COLLAPSE = {
    "population": dict(default=0.6, label="Population floor",
                       help="Collapse when present residents fall below this share of the initial population."),
    "commons": dict(default=True, label="Commons collapse",
                    help="Collapse when the lake falls below its critical stock (depensation)."),
    "revolt_share": dict(default=0.5, label="Revolt share",
                         help="Share of residents protesting that counts as a revolt (Granovetter cascade)."),
    "revolt_weeks": dict(default=2, label="Revolt duration", help="Consecutive weeks of revolt before collapse."),
    "legitimacy": dict(default=0.22, label="Legitimacy floor",
                       help="Mean legitimacy below which the council prepares a no-confidence vote."),
    "legitimacy_weeks": dict(default=4, label="No-confidence delay", help="Consecutive weeks below the floor."),
    "famine_share": dict(default=0.35, label="Famine share", help="Share of residents short of food."),
    "famine_weeks": dict(default=3, label="Famine duration", help="Consecutive weeks of famine before collapse."),
    "fragmentation": dict(default=0.45, label="Fragmentation floor",
                          help="Collapse when the largest connected group is smaller than this share (anomie)."),
}

DOMAINS = {
    "D1": dict(name="Information", unit="informed resident-weeks + forecast bits",
               help="Residents confidently right about live issues each week (+1) or wrong (−1); "
                    "forecasts earn points per bit of information over a coin toss."),
    "D2": dict(name="Economy", unit="coins of net worth created",
               help="Community net worth at the end minus the start: treasury, household coins, food, "
                    "medicine and tools at reference prices."),
    "D3": dict(name="Public works", unit="service-weeks",
               help="Each completed project earns its service value every week it stands."),
    "D4": dict(name="Commons", unit="units harvested + stock change",
               help="Everything fished from the lake plus the change of its stock; overfishing wins early, "
                    "loses the stock."),
    "D5": dict(name="Relationships", unit="trust-weeks",
               help="Sum of residents' trust each week; promises kept (+5) and broken (−10), conflicts "
                    "resolved (+10) and turned violent (−10), concerns relieved (+3)."),
    "D6": dict(name="Institutions", unit="fair legitimacy-weeks",
               help="Residents × mean legitimacy × (1 − Gini) every week."),
    "D7": dict(name="Transmission", unit="skill-weeks",
               help="Best level of every skill kept alive each week, plus 10 per level an apprentice gains."),
    "D8": dict(name="Adaptation", unit="adapted resident-weeks",
               help="After each change, residents using the required convention each week, plus newcomers "
                    "integrated each week."),
}

REFERENCE_PRICES = dict(food=4, medicine=10, tools=8)
