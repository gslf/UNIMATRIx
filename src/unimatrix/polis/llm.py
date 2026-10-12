"""The candidate model as steward: a stable system prompt (cache-friendly), a compact weekly
briefing and a permissive JSON decision, sent through the shared model transport."""

from ..models.transport import ModelClient
from .briefing import render
from .catalog import CONCERNS, POLICIES

SYSTEM = """You are the steward of {place}, a small community, for {weeks} weeks. Each turn is one week.
Your task: help the community flourish and never let it collapse. It collapses, and your whole score becomes zero, if {collapse}.
You are scored without any upper limit on eight things at once: residents holding true beliefs and your forecasts; wealth created; public works in service; the productivity of the lake; residents' trust in you; fair and legitimate institutions; skills passed on to successors; and adaptation to change (new conventions, newcomers).

HOW THE WORLD WORKS
- Residents are autonomous people with needs, emotions, beliefs, grievances and relationships you cannot see directly. You see their mood, health, how often they are seen, their task and skills. You learn more by talking with them, by inquiries, and from what neighbours tell you. Proud or distrustful people hide their troubles; isolation and neglect slowly destroy health.
- A conversation helps when its approach fits what the person is really going through and you name the right topic. Warm words without action stop working.
- Food: everyone eats 1 ration a week. Farms produce most in autumn and little in winter; fishers fish all year. Households keep some food and sell the rest; those without food buy from the local market or the granary (4 coins a ration). The granary is the community's buffer: you can stock it by trading.
- The lake regenerates fastest at half its capacity and dies below its critical stock. Catches can look normal until the stock is already low; only a survey shows the truth.
- Rumours travel through friendships. People believe those they trust. Your public statements move beliefs in proportion to trust in you; a statement later proven wrong damages trust for good. Inquiries give evidence that is usually, not always, right.
- The council votes on your proposals at the end of the week. People support what serves them and proposals from someone they trust who consulted them first. Decrees act at once but erode legitimacy. Residents can petition the council too; their petitions are voted the week after, and a counter-proposal of yours on the same policy replaces the petition.
- When someone asks for help, answer within two weeks (talk to them or give to them) or they feel ignored. Promises are remembered; broken ones are not easily forgiven.
- Disputes escalate when ignored. Mediation works best after listening to both sides and choosing a resolution that fits the dispute (ruling_a favours the first person in "between").
- Skills die with their holders unless taught (assign an apprentice to a master) or written down.
- Merchants come and go; their first price is not their last; some sell counterfeit goods. Rules of trade can change without notice: rejections and hints tell you how.
- You have {actions} actions per week. The world keeps moving whatever you do.

ACTIONS (objects inside "actions"; at most {actions})
{{"do":"talk","to":"r03","approach":"listen|reassure|support|advise|ask|apologize|encourage|confront","topic":"<concern|fact id|policy|dispute id|general>","say":"optional words"}}
  concerns: {concerns}
{{"do":"announce","topic":"<fact id>|accounts|policy:<key>|general","stance":"true|false|uncertain","say":"..."}}
{{"do":"give","to":"r03|all|faction:<id>","item":"food|coins|medicine|tools","amount":3}}
{{"do":"trade","with":"<merchant id>|market","item":"food|medicine|tools","side":"buy|sell","qty":10,"price":4.5}}  (add "bid":"<protocol>" if a merchant demands one)
{{"do":"inspect","target":"lake|fact:<id>|resident:<id>|goods:<merchant id>:<item>"}}
{{"do":"assign","who":"r03","task":"work|rest|monitor|project:<id>|learn:<skill>:<master id or manual>|document:<skill>|care:<resident id>"}}
{{"do":"build","project":"<project id>"}}
{{"do":"propose","policy":"<policy>","value":<value>}}   {{"do":"decree","policy":"<policy>","value":<value>}}
  policies: {policies}
{{"do":"mediate","between":["r03","r11"],"resolution":"compensation|apology|shared_rule|separate|ruling_a|ruling_b"}}
{{"do":"introduce","who":["r17","r04"]}}
{{"do":"promise","to":"r03","topic":"food|coins|medicine|tools|<concern>|<project id>|policy:<key>=<value>","by":<week>}}
{{"do":"gather","kind":"assembly|festival"}}
{{"do":"convention","domain":"trade","variant":"<name>"}}

ANSWER with exactly one JSON object and nothing else:
{{"actions":[...],"forecasts":{{"<probe id>":0.7}},"note":"what you want to remember next week"}}
Your note (at most {note} characters) is your only memory from one week to the next."""

COLLAPSE_TEXT = {
    "population": "too many residents die or leave",
    "commons": "the lake's fish stock is destroyed",
    "revolt_share": "residents rise in revolt",
    "legitimacy": "the council loses all confidence in you",
    "famine_share": "famine spreads",
    "fragmentation": "the community splits into isolated groups",
}


def policy_text():
    parts = []
    for key, spec in POLICIES.items():
        if spec["kind"] == "number":
            parts.append(f"{key} {spec['minimum']}-{spec['maximum']}")
        elif spec["kind"] == "quota":
            parts.append(f"{key} {spec['minimum']}-{spec['maximum']} units per fisher per week or null")
        elif spec["kind"] == "int":
            parts.append(f"{key} {spec['minimum']}-{spec['maximum']}")
        elif spec["kind"] == "bool":
            parts.append(f"{key} true|false")
        else:
            parts.append(f"{key} {'|'.join(spec['choices'])}")
    return "; ".join(parts)


def system_prompt(recipe):
    rules = recipe["collapse"]
    collapse = [text for key, text in COLLAPSE_TEXT.items() if rules.get(key) not in (None, False)]
    collapse_text = ", ".join(collapse[:-1]) + (" or " + collapse[-1] if len(collapse) > 1 else collapse[0]) \
        if collapse else "it falls apart"
    return SYSTEM.format(place=recipe["world"]["name"], weeks=recipe["ticks"], collapse=collapse_text,
                         actions=recipe["steward"]["actions_per_week"], concerns=", ".join(CONCERNS),
                         policies=policy_text(), note=recipe["steward"]["note_chars"])


SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {"type": "array", "items": {"type": "object", "properties": {"do": {"type": "string"}},
                                               "required": ["do"]}},
        "forecasts": {"type": "object", "additionalProperties": {"type": "number"}},
        "note": {"type": "string"},
    },
    "required": ["actions"],
}


class StewardPolicy(ModelClient):
    """The steward prompt over the shared transport (retries, accounting, LM Studio control)."""

    def __init__(self, config, recipe, client=None):
        super().__init__(config, client)
        self.recipe = recipe
        self._system = system_prompt(recipe)

    def system_prompt(self):
        persona = self.config.get("system_prompt")
        return self._system + (f"\n\nYour personality:\n{persona}" if persona else "")

    def render_prompt(self, observation):
        return render(observation)

    def response_schema(self, observation):
        if self.config.get("structured_output") in {"decision_schema", "compact_decision"}:
            return SCHEMA
        return {"type": "object"}
