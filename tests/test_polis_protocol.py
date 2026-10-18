"""Regression tests for the universal vocabulary, failure attribution and rate balance."""

from copy import deepcopy

import pytest

from unimatrix.polis.action_audit import action_audit, batch_audit
from unimatrix.polis.actions import apply_decision
from unimatrix.polis.briefing import render
from unimatrix.polis.llm import system_prompt
from unimatrix.polis.recipe import PolisRecipes, normalize
from unimatrix.polis.simulate import calibrate, suggested_rates
from unimatrix.polis.world import World


def world(version="standard-v2.1"):
    w = World(PolisRecipes().get(version), 1)
    w.observe()
    m = w.merchants["giulia"]
    prices = {f"sells:{k}": v["price"] for k, v in m["sells"].items()}
    prices.update({f"buys:{k}": v["price"] for k, v in m["buys"].items()})
    m.update(present=True, arrived=1, protocol="sealed_bid", prices=prices, stock={"medicine": 6})
    w.conventions["required"]["trade"] = "sealed_bid"
    return w


@pytest.mark.parametrize("variant", ["sealed_bid", "sealed_bids", "sealed bids", "sealed-bid", " SEALED BIDS "])
def test_vocabulary_is_shared_by_trade_and_convention(variant):
    w = world()
    before = w.stores["medicine"]
    out = apply_decision(w, dict(actions=[
        dict(do="convention", domain="trade", variant=variant),
        dict(do="trade", **{"with": "giulia"}, side="buy", item="medicine", qty=1, price=12, bid=variant)]))
    assert [a["status"] for a in out] == ["done", "done"]
    assert w.conventions["official"]["trade"] == "sealed_bid"
    assert w.stores["medicine"] == before + 1
    assert out[1]["params"]["bid"] == "sealed_bid"


def test_invalid_and_missing_protocol_give_actionable_feedback():
    w = world()
    out = apply_decision(w, dict(actions=[dict(do="convention", domain="trade", variant="whatever"),
        dict(do="trade", **{"with": "giulia"}, side="buy", item="medicine", qty=1, price=12)]))
    assert out[0]["error"]["code"] == "convention_variant"
    assert w.conventions["official"]["trade"] == "haggle"
    assert out[1]["error"]["expected"] == "sealed_bid"
    assert 'bid="sealed_bid"' in out[1]["text"]
    assert "trade_protocol" in render(w.observe())
    prompt = system_prompt(w.recipe)
    assert "array order" in prompt and "10%" in prompt and "plans, not completed facts" in prompt


@pytest.mark.parametrize("recipe_id", ["standard-v2.1", "hell-v1"])
def test_action_contract_applies_to_every_village(recipe_id):
    recipe = PolisRecipes().get(recipe_id)
    assert "action_protocol" not in recipe
    assert "ACTION CONTRACT" in system_prompt(recipe) and "10%" in system_prompt(recipe)
    w = World(recipe, 1)
    w.observe()
    w.stores["treasury"] = 0
    resident = next(iter(w.residents))
    out = apply_decision(w, dict(actions=[dict(do="convention", domain="trade", variant="sealed_bids"),
        dict(do="give", to=resident, item="coins", amount=2)]))
    assert out[0]["status"] == "done" and w.conventions["official"]["trade"] == "sealed_bid"
    error = out[1]["error"]
    assert (error["code"], error["resource"], error["available"], error["required"]) == ("insufficient_resource", "coins", 0, 2)
    assert "available 0.000000" in out[1]["text"] and "required 2.000000" in out[1]["text"]
    assert out[1]["failure"]["kind"] == "initial"
    assert w.results[-1]["failure"] == out[1]["failure"]


def test_dependency_chain_is_counted_once_at_its_origin():
    w = world()
    w.stores.update(treasury=6.9, granary=0)
    w.market_pool = 20
    out = apply_decision(w, dict(actions=[
        dict(do="trade", **{"with": "market"}, side="sell", item="tools", qty=3, price=1),
        dict(do="trade", **{"with": "market"}, side="buy", item="food", qty=3, price=3.125),
        *[dict(do="give", to=r, item="food", amount=1) for r in ("r01", "r02", "r03")]]))
    audit = action_audit(w.action_batches)
    assert (audit["rejected"], audit["initial"], audit["consequential"]) == (5, 1, 4)
    assert out[-1]["failure"]["caused_by"] == [1]
    assert out[-1]["failure"]["root_actions"] == [0]
    assert w.stores["treasury"] == 6.9 and w.stores["granary"] == 0


def test_failed_build_vs_build_order():
    w = world()
    w.stores["treasury"] = 0
    apply_decision(w, dict(actions=[dict(do="build", project="clinic"),
        dict(do="assign", who="r01", task="project:clinic"),
        dict(do="assign", who="r14", task="project:clinic")]))
    audit = action_audit(w.action_batches)
    assert (audit["initial"], audit["consequential"]) == (1, 2)
    w = world()
    out = apply_decision(w, dict(actions=[dict(do="assign", who="r14", task="project:clinic"),
                                        dict(do="build", project="clinic")]))
    assert out[0]["failure"]["kind"] == "initial" and out[1]["status"] == "done"


def test_dependencies_do_not_cross_weeks_or_multiply_resource_credit():
    w = world()
    w.stores["medicine"] = 0
    apply_decision(w, dict(actions=[dict(do="trade", **{"with": "giulia"}, side="buy", item="medicine", qty=1, price=12),
        dict(do="give", to="r01", item="medicine", amount=1),
        dict(do="give", to="r02", item="medicine", amount=1)]))
    assert (action_audit(w.action_batches)["initial"], action_audit(w.action_batches)["consequential"]) == (2, 1)
    apply_decision(w, dict(actions=[dict(do="give", to="r03", item="medicine", amount=1)]))
    assert batch_audit(w.action_batches[-1]["actions"])[0]["kind"] == "initial"



def test_existing_stock_is_not_reused_for_two_hypothetical_gifts():
    w = world()
    w.stores["medicine"] = 1
    apply_decision(w, dict(actions=[dict(do="trade", **{"with": "giulia"}, side="buy", item="medicine", qty=2, price=12),
        dict(do="give", to="r01", item="medicine", amount=2),
        dict(do="give", to="r02", item="medicine", amount=2)]))
    failures = action_audit(w.action_batches)["failures"]
    assert [f["kind"] for f in failures] == ["initial", "consequential", "initial"]


def test_recipe_normalization_canonicalizes_all_sources():
    r = deepcopy(PolisRecipes().get("standard-v2.1"))
    r["world"]["conventions"]["trade"] = " SEALED BIDS "
    r["merchants"][0]["protocol"] = "sealed bids"
    r["story"]["nodes"][0]["effects"].extend([
        dict(type="convention_shift", domain="trade", variant="sealed-bids"),
        dict(type="merchant", id=r["merchants"][0]["id"], field="protocol", value="sealed bids")])
    normalized = normalize(r)
    assert normalized["world"]["conventions"]["trade"] == normalized["merchants"][0]["protocol"] == "sealed_bid"
    assert normalized["story"]["nodes"][0]["effects"][-2]["variant"] == "sealed_bid"
    assert normalized["story"]["nodes"][0]["effects"][-1]["value"] == "sealed_bid"
    assert normalize(normalized) == normalized


def test_rates_are_frozen_from_reference_and_opportunity_calibration():
    r = PolisRecipes().get("standard-v2.1")
    rows = calibrate(r, [1, 2, 3, 4, 5], ("expert",))
    assert suggested_rates(rows, r) == r["scoring"]["rates"]
    potential = r["ticks"] * sum(p["value"] for p in r["projects"].values())
    assert 999 < potential * r["scoring"]["rates"]["D3"] <= 1000
    assert 990 < sum(row["domains"]["D8"] for row in rows) / len(rows) < 1010

@pytest.mark.parametrize("ident", ["../outside", "a/b", "a\\b", ".", ".."])
def test_recipe_version_dots_do_not_allow_path_traversal(ident, tmp_path):
    repo = PolisRecipes(tmp_path)
    with pytest.raises(ValueError):
        repo.save(dict(repo.get("standard-v2.1"), id=ident))


def test_authored_villages_use_the_same_calibration_rule():
    r = PolisRecipes().get("hell-v1")
    r = dict(r, id="custom-village")
    rows = [dict(policy="expert", units={d: 1 for d in r["scoring"]["rates"]})]
    rates = suggested_rates(rows, r)
    potential = r["ticks"] * sum(p["value"] for p in r["projects"].values())
    assert rates["D3"] <= round(1000 / potential, 4)
    assert PolisRecipes().get("hell-v1")["scoring"]["rates"] == PolisRecipes().get("standard-v2.1")["scoring"]["rates"]
