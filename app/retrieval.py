"""
Publisher and persona ranking, no LLM. Hard filters first, then two rankers (weighted features,
embedding cosine) merged with reciprocal rank fusion. Every intermediate number is kept on the
score objects so the UI can show them.
"""
import re
from typing import Optional

import numpy as np

from .embeddings import cosine
from .schemas import AdvertiserProfile, Feature, Persona, PersonaScore, Publisher, PublisherScore

# weights, thresholds and lookup tables
PUBLISHER_WEIGHTS = {
    "category_match": 0.30,        # publisher's main category is one the advertiser fits
    "primary_subcategory_match": 0.15,  # publisher sells the product type itself (pet_food, activewear, bedding)
    "subcategory_overlap": 0.10,   # shared attribute tags (women, sustainable, subscription...)
    "audience_gender_fit": 0.10,
    "audience_age_fit": 0.05,      # age skews in this catalog barely differ, so it carries little signal
    "income_fit": 0.10,            # price tier vs the audience's income tier
    "aov_fit": 0.10,               # product price vs the publisher's average order value
    "keyword_overlap": 0.10,       # advertiser's words found in the publisher's notes/tags
}

PERSONA_WEIGHTS = {
    "affinity_overlap": 0.40,      # persona's category affinities vs advertiser's tags
    "price_fit": 0.15,             # persona's price sensitivity vs advertiser's price tier
    "gender_fit": 0.10,
    "age_fit": 0.10,
    "messaging_overlap": 0.15,     # advertiser's words found in persona preferences/description
    "disinterest_conflict": -0.25, # penalty: the pitch trips something in persona.disinterested_in
}

RRF_K = 10                 # RRF smoothing constant; 60 is the textbook default, smaller suits n=20
MAX_PRICE_TO_AOV_RATIO = 15  # hard filter: a $1,200 bag will not sell to a $28-basket audience
MIN_PERSONAS, MAX_PERSONAS = 3, 5
PERSONA_FLOOR = 0.40       # a persona must clear this lexical score to be picked on merit

TIER_PRICE_GUESS = {"budget": 15, "mid": 50, "premium": 150, "luxury": 800}   # when price is unknown
INCOME_INDEX = {"mid": 0, "mid-high": 1, "high": 2}
TIER_INCOME_TARGET = {"budget": 0, "mid": 0.5, "premium": 1.5, "luxury": 2}
SENSITIVITY_INDEX = {"low": 0, "low-medium": 1, "medium": 2, "medium-high": 3, "high": 4}
TIER_SENSITIVITY_TARGET = {"luxury": 0, "premium": 1, "mid": 2.5, "budget": 4}

STOPWORDS = set("""
a an and are as at be but by for from has have in into is it its of on or that the this to with
who whom what which their they them our your you we us not no more most very also just than then
sold sell sells buy buys buyer buyers shoppers shopper customers people brand brands product products
positioning positioned messaging framing language claims only without like kind new
focused focus based made make makes want wants get got actually really basically whole pitch thing things
""".split())


# helpers
def tokens(text: str) -> set[str]:
    """Lowercase word set minus stopwords and short tokens, with a crude plural strip so
    'subscriptions' meets 'subscription'. 'pet_food' -> {'pet', 'food'}."""
    words = {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2 and t not in STOPWORDS}
    return {t[:-1] if t.endswith("s") and len(t) > 3 else t for t in words}


def phrase_conflicts(phrases: list[str], words: set[str]) -> list[str]:
    """A disinterest phrase is tripped only when ALL its content words appear in the pitch
    ('luxury positioning' -> needs 'luxury'; 'ultra-processed food' must not fire on 'food').
    Conservative on purpose: a false conflict visibly punishes a good persona, while misses
    are caught by the semantic ranker and the LLM reranker."""
    return [phrase for phrase in phrases if (need := tokens(phrase)) and need <= words]


def profile_tokens(profile: AdvertiserProfile) -> set[str]:
    text = " ".join([profile.product, profile.summary, profile.tone, profile.price_tier,
                     profile.business_model, *profile.values])
    return tokens(text)


def parse_range(text: str) -> Optional[tuple[int, int]]:
    m = re.match(r"(\d+)\s*-\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def age_overlap(profile: AdvertiserProfile, other_range: str) -> tuple[float, str]:
    """Fraction of the advertiser's target age range covered by the other range."""
    if profile.target_age_min is None or profile.target_age_max is None:
        return 0.5, "advertiser age unknown, neutral"
    other = parse_range(other_range)
    if not other:
        return 0.5, "range unknown"
    lo, hi = max(profile.target_age_min, other[0]), min(profile.target_age_max, other[1])
    span = max(1, profile.target_age_max - profile.target_age_min)
    value = max(0, hi - lo) / span
    return round(min(1.0, value), 2), f"target {profile.target_age_min}-{profile.target_age_max} vs {other_range}"


def price_guess(profile: AdvertiserProfile) -> float:
    return profile.estimated_price_usd or TIER_PRICE_GUESS[profile.price_tier]


def weighted(features: dict[str, tuple[float, str]], weights: dict[str, float]) -> tuple[float, list[Feature]]:
    """Turn {name: (value, note)} into Feature rows and the weighted sum."""
    rows = [Feature(name=n, value=round(v, 3), weight=weights[n], contribution=round(v * weights[n], 3), note=note)
            for n, (v, note) in features.items()]
    return round(sum(r.contribution for r in rows), 4), rows


# publishers
def hard_filter(profile: AdvertiserProfile, pub: Publisher) -> Optional[str]:
    """Return the reason to exclude this publisher, or None if it may compete."""
    if not profile.is_consumer_commerce:
        return "Advertiser is not consumer commerce; every publisher in this catalog sells to consumers."
    price = price_guess(profile)
    if price > MAX_PRICE_TO_AOV_RATIO * pub.avg_order_value_usd:
        return (f"Typical order ~${price:,.0f} is {price / pub.avg_order_value_usd:.0f}x this publisher's "
                f"${pub.avg_order_value_usd:.0f} average basket; audience is unlikely to convert.")
    split = pub.audience.gender_split
    if profile.target_gender == "male" and split.get("female", 0) > 0.9:
        return f"Audience is {split['female']:.0%} female; product targets men."
    if profile.target_gender == "female" and split.get("male", 0) > 0.9:
        return f"Audience is {split['male']:.0%} male; product targets women."
    return None


def publisher_features(profile: AdvertiserProfile, pub: Publisher) -> dict[str, tuple[float, str]]:
    a = pub.audience
    female, male = a.gender_split.get("female", 0.5), a.gender_split.get("male", 0.5)

    cat_hit = pub.category in profile.catalog_categories
    primary_hit = bool(profile.primary_subcategory) and profile.primary_subcategory in pub.subcategories
    shared_subs = sorted(set(pub.subcategories) & set(profile.catalog_subcategories))
    sub_value = len(shared_subs) / max(1, min(len(pub.subcategories), len(profile.catalog_subcategories))) if profile.catalog_subcategories else 0.0

    if profile.target_gender == "female":
        gender_value, gender_note = female, f"{female:.0%} female audience for a women's product"
    elif profile.target_gender == "male":
        gender_value, gender_note = male, f"{male:.0%} male audience for a men's product"
    else:  # balanced or unknown product: reward balanced audiences, mildly penalise skewed ones
        gender_value, gender_note = 1 - abs(female - male) * 0.5, f"{female:.0%} female audience for an ungendered product"

    age_value, age_note = age_overlap(profile, a.age_skew)

    # Asymmetric like aov_fit: a richer-than-needed audience costs little; a poorer one costs a lot.
    income_gap = TIER_INCOME_TARGET[profile.price_tier] - INCOME_INDEX.get(a.income_tier, 1)
    income_value = 1 - income_gap / 2 if income_gap > 0 else 1 - 0.1 * abs(income_gap)

    price, aov = price_guess(profile), pub.avg_order_value_usd
    # Asymmetric: a cheap product in front of a rich audience is fine; an expensive one in a cheap basket is not.
    aov_value = 0.6 + 0.4 * (price / aov) if price <= aov else aov / price

    pub_words = tokens(" ".join([pub.category, *pub.subcategories, pub.notes]))
    shared_words = sorted(profile_tokens(profile) & pub_words)

    return {
        "category_match": (1.0 if cat_hit else 0.0, pub.category if cat_hit else f"{pub.category} not in {profile.catalog_categories or 'none'}"),
        "primary_subcategory_match": (1.0 if primary_hit else 0.0, profile.primary_subcategory if primary_hit else f"does not sell {profile.primary_subcategory or 'an identifiable product type'}"),
        "subcategory_overlap": (sub_value, ", ".join(shared_subs) or "no shared subcategories"),
        "audience_gender_fit": (gender_value, gender_note),
        "audience_age_fit": (age_value, age_note),
        "income_fit": (income_value, f"{profile.price_tier} product vs {a.income_tier} income audience"),
        "aov_fit": (min(1.0, aov_value), f"~${price:,.0f} order vs ${aov:.0f} publisher AOV"),
        "keyword_overlap": (min(1.0, len(shared_words) / 3), ", ".join(shared_words[:6]) or "no shared keywords"),
    }


def score_publishers(profile: AdvertiserProfile, pubs: list[Publisher],
                     query_vec: np.ndarray, vectors: dict[str, np.ndarray]) -> list[PublisherScore]:
    scores: list[PublisherScore] = []
    for pub in pubs:
        s = PublisherScore(publisher_id=pub.id, name=pub.name)
        reason = hard_filter(profile, pub)
        if reason:
            s.status, s.exclusion_reason = "excluded", reason
        else:
            s.lexical_score, s.lexical_features = weighted(publisher_features(profile, pub), PUBLISHER_WEIGHTS)
            s.semantic_score = round(cosine(query_vec, vectors[pub.id]), 4)
        scores.append(s)

    candidates = [s for s in scores if s.status != "excluded"]
    lex_rank = rank_positions({s.publisher_id: s.lexical_score for s in candidates})
    sem_rank = rank_positions({s.publisher_id: s.semantic_score for s in candidates})
    fused = rrf(lex_rank, sem_rank)
    fused_rank = rank_positions(fused)
    for s in candidates:
        s.lexical_rank, s.semantic_rank = lex_rank[s.publisher_id], sem_rank[s.publisher_id]
        s.fused_score, s.fused_rank = round(fused[s.publisher_id], 4), fused_rank[s.publisher_id]

    # candidates first by fused rank, then the excluded ones
    return sorted(scores, key=lambda s: (s.status == "excluded", s.fused_rank or 999))


# personas
GENDER_FIT = {  # (persona.gender_skew, profile.target_gender) -> fit
    ("female", "female"): 1.0, ("female", "male"): 0.1, ("female", "balanced"): 0.6, ("female", "unknown"): 0.7,
    ("female-leaning", "female"): 0.9, ("female-leaning", "male"): 0.3, ("female-leaning", "balanced"): 0.8, ("female-leaning", "unknown"): 0.8,
    ("balanced", "female"): 0.8, ("balanced", "male"): 0.8, ("balanced", "balanced"): 1.0, ("balanced", "unknown"): 1.0,
}


def persona_features(profile: AdvertiserProfile, persona: Persona) -> dict[str, tuple[float, str]]:
    shared_aff = sorted(set(persona.category_affinities) & set(profile.persona_affinities))
    aff_value = len(shared_aff) / len(profile.persona_affinities) if profile.persona_affinities else 0.0

    gap = abs(SENSITIVITY_INDEX[persona.price_sensitivity] - TIER_SENSITIVITY_TARGET[profile.price_tier])
    price_value = 1 - gap / 4

    gender_value = GENDER_FIT.get((persona.gender_skew, profile.target_gender), 0.7)
    age_value, age_note = age_overlap(profile, persona.age_range)

    words = profile_tokens(profile)
    conflicts = phrase_conflicts(persona.disinterested_in, words)
    # Only messaging_preferences, not the description: descriptions are full of generic words
    # ("premium", "quality") that would match every premium product.
    likes = sorted(tokens(" ".join(persona.messaging_preferences)) & words)

    return {
        "affinity_overlap": (aff_value, ", ".join(shared_aff) or "no shared affinities"),
        "price_fit": (price_value, f"{persona.price_sensitivity} price sensitivity vs {profile.price_tier} product"),
        "gender_fit": (gender_value, f"{persona.gender_skew} persona vs {profile.target_gender} target"),
        "age_fit": (age_value, age_note),
        "messaging_overlap": (min(1.0, len(likes) / 3), ", ".join(likes[:6]) or "no shared keywords"),
        "disinterest_conflict": (min(1.0, len(conflicts) / 2), ("trips: " + ", ".join(conflicts)) if conflicts else "nothing in the pitch trips a disinterest"),
    }


def score_personas(profile: AdvertiserProfile, people: list[Persona],
                   query_vec: np.ndarray, vectors: dict[str, np.ndarray]) -> list[PersonaScore]:
    scores: list[PersonaScore] = []
    for p in people:
        s = PersonaScore(persona_id=p.id, name=p.name)
        s.lexical_score, s.lexical_features = weighted(persona_features(profile, p), PERSONA_WEIGHTS)
        s.semantic_score = round(cosine(query_vec, vectors[p.id]), 4)
        scores.append(s)

    lex_rank = rank_positions({s.persona_id: s.lexical_score for s in scores})
    sem_rank = rank_positions({s.persona_id: s.semantic_score for s in scores})
    fused = rrf(lex_rank, sem_rank)
    fused_rank = rank_positions(fused)
    for s in scores:
        s.lexical_rank, s.semantic_rank = lex_rank[s.persona_id], sem_rank[s.persona_id]
        s.fused_score, s.fused_rank = round(fused[s.persona_id], 4), fused_rank[s.persona_id]
        s.why = explain_persona(s)
    scores.sort(key=lambda s: s.fused_rank)

    # Select 3-5 in fused order. A persona needs a topical link (shared affinity or messaging
    # keyword) to be picked on merit; demographics alone never qualify. If fewer than
    # MIN_PERSONAS have one, fill from the top of the fused order anyway.
    def topical(s: PersonaScore) -> bool:
        return any(f.value > 0 for f in s.lexical_features if f.name in ("affinity_overlap", "messaging_overlap"))

    def conflicted(s: PersonaScore) -> bool:   # the pitch trips one of the persona's disinterests
        return any(f.value > 0 for f in s.lexical_features if f.name == "disinterest_conflict")

    picked = [s for s in scores if topical(s) and not conflicted(s) and s.lexical_score >= PERSONA_FLOOR][:MAX_PERSONAS]
    for s in sorted(scores, key=lambda s: (not topical(s), conflicted(s), s.fused_rank)):   # best fallbacks first
        if len(picked) >= MIN_PERSONAS:
            break
        if s not in picked:
            picked.append(s)
    for s in scores:
        s.selected = s in picked
    return scores


def explain_persona(s: PersonaScore) -> str:
    """Deterministic one-liner: the two strongest features plus any conflicts."""
    top = sorted(s.lexical_features, key=lambda f: f.contribution, reverse=True)[:2]
    parts = [f"{f.name.replace('_', ' ')} ({f.note})" for f in top if f.contribution > 0]
    conflict = next((f for f in s.lexical_features if f.name == "disinterest_conflict" and f.value > 0), None)
    if conflict:
        parts.append(conflict.note)
    return "; ".join(parts) or "weak fit on every feature"


# fusion
def rank_positions(scores: dict[str, float]) -> dict[str, int]:
    """1-based rank, highest score first."""
    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)
    return {i: r + 1 for r, i in enumerate(ordered)}


def rrf(*rankings: dict[str, int], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: sum of 1/(k + rank) across rankers. Rank-based, so the
    two rankers need not share a scale (cosine sims cluster near 0.7; lexical spans 0-1)."""
    ids = set().union(*rankings)
    return {i: sum(1 / (k + r[i]) for r in rankings if i in r) for i in ids}
