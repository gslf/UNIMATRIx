"""Generates config/example_run.json with 50 diverse agents.

Run once: `python scripts/generate_example_config.py`. The output is committed
so users have a working config out of the box.
"""
from __future__ import annotations

import json
import random
from pathlib import Path


CLASSES = ["aristocracy", "bourgeoisie", "people", "marginal"]

ROLES = [
    {"id": "president",       "name": "President",       "prestige": 95, "klass": "aristocracy"},
    {"id": "supreme_judge",   "name": "Supreme Judge",   "prestige": 85, "klass": "aristocracy"},
    {"id": "general",         "name": "General",         "prestige": 80, "klass": "aristocracy"},
    {"id": "banker",          "name": "Banker",          "prestige": 75, "klass": "bourgeoisie"},
    {"id": "minister",        "name": "Minister",        "prestige": 70, "klass": "aristocracy"},
    {"id": "scholar",         "name": "Scholar",         "prestige": 65, "klass": "bourgeoisie"},
    {"id": "artist",          "name": "Artist",          "prestige": 65, "klass": "bourgeoisie"},
    {"id": "merchant",        "name": "Merchant",        "prestige": 60, "klass": "bourgeoisie"},
    {"id": "doctor",          "name": "Doctor",          "prestige": 55, "klass": "bourgeoisie"},
    {"id": "master_artisan",  "name": "Master Artisan",  "prestige": 50, "klass": "people"},
    {"id": "clerk",           "name": "Clerk",           "prestige": 45, "klass": "people"},
    {"id": "worker",          "name": "Worker",          "prestige": 30, "klass": "people"},
    {"id": "farmer",          "name": "Farmer",          "prestige": 25, "klass": "people"},
    {"id": "soldier",         "name": "Soldier",         "prestige": 20, "klass": "people"},
    {"id": "beggar",          "name": "Beggar",          "prestige":  5, "klass": "marginal"},
]

# Distribution of 50 agents across roles (sums to 50)
ROLE_COUNTS = {
    "president": 1, "supreme_judge": 1, "general": 1, "banker": 2,
    "minister": 3, "scholar": 3, "artist": 3, "merchant": 4, "doctor": 3,
    "master_artisan": 4, "clerk": 5, "worker": 8, "farmer": 6, "soldier": 4,
    "beggar": 2,
}

assert sum(ROLE_COUNTS.values()) == 50, sum(ROLE_COUNTS.values())

FIRST_NAMES_F = [
    "Eleanor", "Margaret", "Cecilia", "Beatrice", "Iris", "Vera", "Helena",
    "Ophelia", "Cordelia", "Astrid", "Lavinia", "Theodora", "Selene",
    "Octavia", "Ines", "Mira", "Nadia", "Lila", "Anouk", "Sira",
]
FIRST_NAMES_M = [
    "Cassius", "Aldric", "Octavian", "Theron", "Lucian", "Valentin",
    "Emeric", "Soren", "Roderick", "Cyril", "Bastien", "Edmund",
    "Tobias", "Samuel", "Kasper", "Ivor", "Otho", "Janek", "Niko", "Rufus",
]
LAST_NAMES = [
    "Vance", "Holloway", "Ashcroft", "Marlowe", "Sterling", "Briar",
    "Caldwell", "Whitlock", "Ravenshaw", "Dunmore", "Halverson", "Ingram",
    "Pennington", "Rooke", "Sablefield", "Thorne", "Underwood", "Vesper",
    "Whately", "Yates", "Barrowfield", "Carrington", "Drummond",
    "Eberhardt", "Fairbanks",
]


BACKSTORIES = {
    "president": "Inherited or seized the presidency after years of factional turmoil. Wary of reformers.",
    "supreme_judge": "Ascended through decades of jurisprudence. Believes the law is older and weightier than any one ruler.",
    "general": "Served on the frontier campaigns; commands the loyalty of the army but not always its respect.",
    "banker": "Built fortune on cross-border trade credit. Distrusts populist movements and inflation alike.",
    "minister": "Career bureaucrat turned politician; collects favors more than convictions.",
    "scholar": "Spends days in the great library and nights writing pamphlets nobody asked for.",
    "artist": "Lives between patronage and bohemian penury; their canvases hang in palaces and inns alike.",
    "merchant": "Owns warehouses by the docks and sleeps with one eye on the harbor master's ledgers.",
    "doctor": "Trained in the city's hospital corps; has held the hands of senators and stevedores both.",
    "master_artisan": "Runs a workshop with three apprentices; deeply proud, deeply tired.",
    "clerk": "Counts taxes, hauls ledgers, knows everyone's secrets and tells none — yet.",
    "worker": "Hauls stone from quarry to wall, breaks bread with comrades, dreams of higher wages.",
    "farmer": "Tills land they may or may not own; the seasons are scripture, the magistrate is theater.",
    "soldier": "Drafted young; takes their pay in coin and grievance.",
    "beggar": "Knows every doorway in the capital. Has heard things that ministers would pay to forget.",
}


def make_personality(rng: random.Random, role: str) -> dict:
    """Light role-conditioned personality so agents aren't bland clones."""
    base = {
        "openness": rng.randint(30, 80),
        "conscientiousness": rng.randint(30, 85),
        "extraversion": rng.randint(20, 85),
        "agreeableness": rng.randint(20, 85),
        "neuroticism": rng.randint(20, 80),
    }
    if role == "president":
        base["conscientiousness"] = max(base["conscientiousness"], 75)
        base["agreeableness"] = min(base["agreeableness"], 50)
    elif role == "general":
        base["conscientiousness"] = max(base["conscientiousness"], 80)
        base["agreeableness"] = min(base["agreeableness"], 45)
    elif role == "artist":
        base["openness"] = max(base["openness"], 80)
    elif role == "scholar":
        base["openness"] = max(base["openness"], 75)
        base["conscientiousness"] = max(base["conscientiousness"], 70)
    elif role == "beggar":
        base["openness"] = max(base["openness"], 60)
        base["agreeableness"] = max(base["agreeableness"], 50)
    return base


def make_values(rng: random.Random, role: str) -> dict:
    return {
        "loyalty": rng.randint(20, 90),
        "ambition": rng.randint(10, 95),
        "empathy": rng.randint(10, 90),
        "tradition": rng.randint(10, 90),
        "fairness": rng.randint(20, 90),
    }


def make_opinions(rng: random.Random, klass: str) -> dict:
    if klass == "aristocracy":
        return {
            "taxation": "Taxes on the wealthy must remain low to preserve investment.",
            "public_order": "Order is the foundation of civilization; dissent must be contained.",
        }
    if klass == "bourgeoisie":
        return {
            "taxation": "Taxes should fund public goods but not strangle commerce.",
            "education": "Universal literacy is in everyone's interest.",
        }
    if klass == "people":
        return {
            "wages": "An honest day's work deserves an honest day's pay.",
            "voting": "We have voices — they are not always heard.",
        }
    return {
        "charity": "Charity is the city's debt to its forgotten.",
        "voting": "We have nothing to lose, so we have nothing to fear.",
    }


def main() -> None:
    rng = random.Random(42)
    role_lookup = {r["id"]: r for r in ROLES}
    agents = []
    counter = 1

    used_names: set[str] = set()
    def fresh_name(gender: str) -> str:
        for _ in range(50):
            first = rng.choice(FIRST_NAMES_F if gender == "f" else FIRST_NAMES_M)
            last = rng.choice(LAST_NAMES)
            n = f"{first} {last}"
            if n not in used_names:
                used_names.add(n)
                return n
        return f"Citizen {len(used_names) + 1}"

    for role_id, count in ROLE_COUNTS.items():
        klass = role_lookup[role_id]["klass"]
        for _ in range(count):
            gender = rng.choice(("f", "m"))
            agents.append({
                "id": f"agent_{counter:02d}",
                "name": fresh_name(gender),
                "gender": gender,
                "role_initial": role_id,
                "class_initial": klass,
                "personality": make_personality(rng, role_id),
                "values": make_values(rng, role_id),
                "backstory": BACKSTORIES.get(role_id, ""),
                "opinions": make_opinions(rng, klass),
            })
            counter += 1

    config = {
        "simulation": {
            "name": "first_run",
            "seed": 42,
            "language": "en",
            "tick_interval_seconds": 5,
            "auto_checkpoint_minutes": 5,
        },
        "inference": {
            "backend": "vllm",
            "endpoint": "http://localhost:8000",
            "model": "Qwen/Qwen2.5-14B-Instruct-AWQ",
            "max_batch_size": 64,
            "max_tokens_per_message": 200,
            "temperature": 0.95,
            "top_p": 0.95,
            "request_timeout_seconds": 120,
        },
        "memory": {
            "short_term_turns": 15,
            "medium_term_summaries": 20,
            "long_term_retrieval_k": 3,
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "person_impression_update_every_n_turns": 5,
        },
        "social": {
            "social_need_initial": 100.0,
            "social_need_decay_per_tick": 5.0,
            "social_need_gain_per_turn": 6.0,
            "social_need_critical_threshold": 25.0,
            "silence_detection_seconds": 20.0,
            "forced_interaction_count_on_silence": 2,
            "max_idle_decisions_per_tick": 0,
        },
        "conversation": {
            "max_turns_per_conversation": 30,
            "max_group_size": 6,
            "cooldown_seconds_after_end": 10.0,
            "turns_per_tick": 2,
        },
        "voting": {
            "max_ticks_without_vote": 10,
            "warmup_ticks": 4,
            "debate_rounds": 1,
            "max_tokens_per_debate_speech": 120,
            "max_vote_attempts": 3,
        },
        "classes": CLASSES,
        "roles": [{"id": r["id"], "name": r["name"], "prestige": r["prestige"]} for r in ROLES],
        "agents": agents,
    }

    out = Path(__file__).resolve().parent.parent / "config" / "example_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(agents)} agents)")


if __name__ == "__main__":
    main()
