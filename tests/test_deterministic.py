"""
Unit tests for the parts that need no LLM: hard filters, features, fusion, persona
selection and budget maths. Run with:  .venv/bin/python -m pytest -q
"""
import numpy as np
import pytest

from app.campaign import allocate, reach_cap
from app.catalog import personas, publishers
from app.retrieval import (hard_filter, phrase_conflicts, rank_positions, rrf,
                           score_personas, score_publishers, tokens)
from app.schemas import AdvertiserProfile, PublisherScore


def profile(**overrides) -> AdvertiserProfile:
    base = dict(
        clarity="high", is_consumer_commerce=True,
        summary="Premium grain-free dog food for senior dogs sold by subscription to health-conscious owners.",
        product="senior dog food", catalog_categories=["pet"], catalog_subcategories=["pet_food", "subscription"],
        primary_subcategory="pet_food", persona_affinities=["pet_food", "pet_health", "subscription_boxes"],
        price_tier="premium", estimated_price_usd=80, business_model="subscription", target_gender="balanced",
        target_age_min=30, target_age_max=60, values=["vet-formulated", "joint health"], tone="premium, warm",
        restrictions=[], assumptions=[], clarifying_questions=[],
    )
    base.update(overrides)
    return AdvertiserProfile(**base)


def fake_vectors() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Deterministic stand-in for embeddings: every catalog item gets a random unit vector."""
    rng = np.random.default_rng(0)
    vecs = {}
    for item in [*publishers(), *personas()]:
        v = rng.normal(size=8)
        vecs[item.id] = v / np.linalg.norm(v)
    q = rng.normal(size=8)
    return q / np.linalg.norm(q), vecs


def by_name(name: str):
    return next(p for p in publishers() if p.name == name)


# --- hard filters ------------------------------------------------------------

def test_b2b_excludes_every_publisher():
    p = profile(is_consumer_commerce=False, business_model="b2b", catalog_categories=[])
    assert all(hard_filter(p, pub) for pub in publishers())


def test_luxury_price_excludes_low_basket_publishers_only():
    p = profile(price_tier="luxury", estimated_price_usd=1200)
    assert hard_filter(p, by_name("Swiftcart"))            # $28 basket
    assert hard_filter(p, by_name("Marlowe & Co.")) is None  # $112 basket, 10.7x is under the 15x line


def test_mens_product_excludes_near_all_female_audience():
    p = profile(target_gender="male")
    assert hard_filter(p, by_name("Linden Park"))         # 98% female
    assert hard_filter(p, by_name("Cloudfoot")) is None   # 52/47 split


# --- lexical features -----------------------------------------------------------

def test_tokens_strip_stopwords_and_plurals():
    assert tokens("Owners love subscriptions for the dogs") == {"owner", "love", "subscription", "dog"}


def test_phrase_conflict_requires_every_content_word():
    words = tokens("premium dog food subscription")
    assert phrase_conflicts(["subscription-only", "ultra-processed food"], words) == ["subscription-only"]


def test_pet_food_publishers_outrank_everything_lexically():
    q, vecs = fake_vectors()
    scores = score_publishers(profile(), publishers(), q, vecs)
    lexical_order = sorted((s for s in scores if s.status != "excluded"), key=lambda s: s.lexical_rank)
    assert {lexical_order[0].name, lexical_order[1].name} == {"Pawline", "Ruffco"}
    pawline = next(s for s in scores if s.name == "Pawline")
    assert {f.name for f in pawline.lexical_features} >= {"category_match", "primary_subcategory_match", "aov_fit"}
    assert abs(sum(f.contribution for f in pawline.lexical_features) - pawline.lexical_score) < 1e-6


# --- fusion -----------------------------------------------------------------------

def test_rrf_rewards_agreement_between_rankers():
    lex = rank_positions({"a": 0.9, "b": 0.8, "c": 0.1})
    sem = rank_positions({"a": 0.7, "c": 0.9, "b": 0.2})
    fused = rrf(lex, sem)
    assert fused["a"] > fused["c"] > fused["b"]   # a is 1st+2nd, c is 3rd+1st, b is 2nd+3rd


# --- personas -----------------------------------------------------------------------

def test_persona_selection_is_between_three_and_five_and_pet_parent_leads():
    q, vecs = fake_vectors()
    scores = score_personas(profile(), personas(), q, vecs)
    selected = [s for s in scores if s.selected]
    assert 3 <= len(selected) <= 5
    assert min(scores, key=lambda s: s.lexical_rank).name == "The Pet Parent"


def test_persona_with_tripped_disinterest_is_not_selected_on_merit():
    q, vecs = fake_vectors()
    luxury = profile(price_tier="luxury", estimated_price_usd=1200, product="Italian leather handbag",
                     summary="Custom-fit luxury leather handbags handcrafted in Florence.", values=["craftsmanship", "luxury"],
                     catalog_categories=["apparel"], catalog_subcategories=["women", "classic"], primary_subcategory="classic",
                     persona_affinities=["apparel", "fashion"], target_gender="female")
    scores = score_personas(luxury, personas(), q, vecs)
    value = next(s for s in scores if s.name == "The Value-Conscious Shopper")
    assert any(f.name == "disinterest_conflict" and f.value > 0 for f in value.lexical_features)
    assert not value.selected


def test_claim_checker_flags_invented_offers_and_endorsements():
    from app.creatives import Creative, check
    detergent = profile(product="refillable laundry detergent tablets", price_tier="mid", estimated_price_usd=20,
                        summary="Unscented refillable laundry detergent tablets for apartment households.",
                        values=["refillable", "unscented"], restrictions=["no discounts", "no certifications", "no performance studies"],
                        catalog_categories=["home"], catalog_subcategories=["household"], primary_subcategory="household",
                        persona_affinities=["household", "refillable_products"], business_model="one_time")
    copy = [Creative(persona_id="persona_008", headline="Bundle now for extra savings", body="Trusted by parents, clinically proven clean.",
                     cta="Shop now", angle="x", leaned_into=[], avoided=[])]
    kinds = sorted(w["warning"].split(" ")[1] for w in check(copy, detergent))
    assert kinds == ["endorsement", "offer", "proof"]
    clean = [Creative(persona_id="persona_008", headline="Refillable tablets, no scent", body="Skip the plastic bottle. Works in any machine.",
                      cta="Try it", angle="x", leaned_into=[], avoided=[])]
    assert check(clean, detergent) == []


# --- budget -----------------------------------------------------------------------

def recs():
    return [(PublisherScore(publisher_id=p.id, name=p.name, fit_score=fit), p)
            for p, fit in [(by_name("Pawline"), 90), (by_name("Ruffco"), 75), (by_name("Hearthstone Goods"), 60)]]


def test_allocation_sums_to_budget_and_respects_reach_caps():
    rows, unallocated = allocate(recs(), budget=20_000)
    assert unallocated == 0 and abs(sum(r["budget_usd"] for r in rows) - 20_000) < 5
    by = {r["name"]: r for r in rows}
    for name in ("Pawline", "Hearthstone Goods"):          # small publishers hit their reach cap
        assert by[name]["capped_by_reach"] and by[name]["budget_usd"] <= reach_cap(by_name(name)) + 1
    assert not by["Ruffco"]["capped_by_reach"] and by["Ruffco"]["budget_usd"] > by["Pawline"]["budget_usd"]


def test_allocation_reports_budget_the_publishers_cannot_absorb():
    rows, unallocated = allocate(recs(), budget=1_000_000)
    capacity = sum(reach_cap(p) for _, p in recs())
    assert all(r["capped_by_reach"] for r in rows)
    assert abs(unallocated - (1_000_000 - capacity)) < 5
