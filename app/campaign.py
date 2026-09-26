"""
Budget split, bid strategy, targeting. Plain arithmetic on constants I had to make up,
since there are no rate cards in the data, so every constant is echoed into the config's assumptions.
"""
from datetime import date, timedelta

from .schemas import AdvertiserProfile, PersonaScore, Publisher, PublisherScore

DEFAULT_BUDGET_USD = 10_000
FLIGHT_DAYS = 30
SHARE_OF_VOICE = 0.10          # we assume we can win at most 10% of a publisher's monthly impressions
INCOME_CPM_USD = {"mid": 6.0, "mid-high": 9.0, "high": 13.0}
CATEGORY_CPM_MULT = {"instant_delivery": 0.8, "pet": 0.9, "meal_kits": 0.9, "beverages": 0.9,
                     "groceries": 1.0, "apparel": 1.0, "wellness_services": 1.0,
                     "home": 1.1, "wellness_dtc": 1.1, "beauty": 1.2}
ASSUMED_CTR = 0.008            # clicks per impression; retail-media placements sit near checkout, so higher than display
ASSUMED_CVR = 0.03             # orders per click for a shopper already in a buying session
TARGET_CPA_SHARE = 0.30        # spend at most 30% of first-order value to win a customer
SUBSCRIPTION_LTV_MULT = 3.0    # a subscriber is worth ~3 first orders, so CPA can be higher
FREQUENCY_CAP = {"impressions": 3, "per": "day"}


def estimate_cpm(pub: Publisher) -> float:
    return round(INCOME_CPM_USD[pub.audience.income_tier] * CATEGORY_CPM_MULT.get(pub.category, 1.0), 2)


def reach_cap(pub: Publisher) -> float:
    """Most we could spend on this publisher over the flight at SHARE_OF_VOICE of its impressions."""
    return pub.monthly_impressions * SHARE_OF_VOICE * estimate_cpm(pub) / 1000 * (FLIGHT_DAYS / 30)


def allocate(recommended: list[tuple[PublisherScore, Publisher]], budget: float) -> tuple[list[dict], float]:
    """Budget proportional to fit_score, capped by what each publisher can deliver (water-filling):
    hand out budget in proportion to fit; whoever hits their cap is frozen and the remainder is
    re-shared among the rest. Returns (rows, unallocated) - unallocated > 0 means the recommended
    publishers cannot absorb the whole budget at the assumed share of voice."""
    caps = {pub.id: reach_cap(pub) for _, pub in recommended}
    weights = {pub.id: max(1, s.fit_score or 1) for s, pub in recommended}
    alloc = {i: 0.0 for i in weights}
    open_ids, remaining = set(weights), float(budget)
    while open_ids and remaining > 1:
        total_w = sum(weights[i] for i in open_ids)
        hit_cap = [i for i in open_ids if alloc[i] + remaining * weights[i] / total_w >= caps[i]]
        if not hit_cap:                                  # nobody is capped: share the rest and stop
            for i in open_ids:
                alloc[i] += remaining * weights[i] / total_w
            remaining = 0
        for i in hit_cap:                                # freeze the capped ones, loop with what is left
            remaining -= caps[i] - alloc[i]
            alloc[i] = caps[i]
            open_ids.remove(i)
    rows = []
    for s, pub in recommended:
        spend = alloc[pub.id]
        cpm = estimate_cpm(pub)
        impressions = spend / cpm * 1000
        orders = impressions * ASSUMED_CTR * ASSUMED_CVR
        rows.append({
            "publisher_id": pub.id, "name": pub.name, "category": pub.category,
            "allocation_pct": round(100 * spend / budget, 1), "budget_usd": round(spend),
            "est_cpm_usd": cpm, "est_impressions": round(impressions), "est_orders": round(orders, 1),
            "est_cpa_usd": round(spend / orders, 2) if orders else None,
            "capped_by_reach": spend >= caps[pub.id] - 1,
            "fit_score": s.fit_score, "rationale": s.rationale,
        })
    return rows, round(max(0.0, remaining))


def objective(profile: AdvertiserProfile) -> str:
    if profile.business_model == "subscription":
        return "subscriptions"
    if profile.price_tier == "luxury":
        return "qualified_consideration"   # nobody buys a $1,200 bag off a text ad; win the visit
    if profile.business_model == "gifting":
        return "seasonal_conversions"
    return "conversions"


def feasibility(strategy: dict, target_cpa: float, cpc: float, rows: list[dict]) -> dict:
    """Reconcile the two sides of a bid: what a click costs (CPM over click rate) versus what a
    click can be worth (target CPA times conversion rate). If cost exceeds worth, say so plainly
    instead of suggesting bids the economics cannot support."""
    max_cpc = round(target_cpa * ASSUMED_CVR, 2)
    ratio = cpc / max_cpc                                  # >1 means a click costs more than it can be worth
    needed_cvr = cpc / target_cpa                          # conversion rate that would make the target hold
    strategy["max_affordable_cpc_usd"] = max_cpc
    strategy["feasible"] = ratio <= 1
    strategy["feasibility"] = "feasible" if ratio <= 1 else "marginal" if ratio <= 1.5 else "infeasible"
    if ratio > 1:
        strategy["feasibility_note"] = (
            f"At {ASSUMED_CVR:.0%} conversion a ${target_cpa:.0f} target CPA affords clicks up to ${max_cpc:.2f}, "
            f"but clicks on these publishers cost about ${cpc:.2f} ({ratio:.1f}x). Holding the target needs a "
            f"{needed_cvr:.1%} conversion rate"
            + (" or a modestly higher order value." if ratio <= 1.5 else
               ", which is unrealistic: this product needs a higher order value, a subscription or bundle, or repeat "
               "purchase behind it before paid placement pays back."))
    return strategy


def bid_strategy(profile: AdvertiserProfile, rows: list[dict]) -> dict:
    avg_cpm = sum(r["est_cpm_usd"] for r in rows) / len(rows)
    cpc = avg_cpm / (1000 * ASSUMED_CTR)
    price = profile.estimated_price_usd or 50
    if profile.price_tier == "luxury":
        return {"pricing_model": "CPM", "strategy": "reach_then_retarget",
                "starting_bid": {"low": round(avg_cpm * 0.9, 2), "high": round(avg_cpm * 1.3, 2), "unit": "USD per 1000 impressions"},
                "rationale": "High-consideration purchase: pay for qualified reach on affluent publishers, then retarget visitors. Optimising to last-click CPA would starve the campaign."}
    if profile.business_model == "subscription":
        target = round(price * TARGET_CPA_SHARE * SUBSCRIPTION_LTV_MULT, 2)
        return feasibility({"pricing_model": "CPA", "strategy": "target_cpa", "target_cpa_usd": target,
                "starting_bid": {"low": round(cpc * 0.8, 2), "high": round(cpc * 1.3, 2), "unit": "USD per click (until CPA data accrues)"},
                "rationale": f"Recurring revenue: a subscriber is assumed worth ~{SUBSCRIPTION_LTV_MULT:.0f}x the first ${price:.0f} order, which sets the ${target:.0f} target CPA. Start on CPC bids while the system learns."},
                target, cpc, rows)
    target = round(price * TARGET_CPA_SHARE, 2)
    return feasibility({"pricing_model": "CPC", "strategy": "max_conversions_with_cpa_guardrail", "target_cpa_usd": target,
            "starting_bid": {"low": round(cpc * 0.8, 2), "high": round(cpc * 1.2, 2), "unit": "USD per click"},
            "rationale": f"One-time purchase around ${price:.0f}: bid per click, cap acquisition cost at {TARGET_CPA_SHARE:.0%} of order value (${target:.0f})."},
            target, cpc, rows)


def build_config(profile: AdvertiserProfile, pub_scores: list[PublisherScore], pubs: list[Publisher],
                 persona_scores: list[PersonaScore], creatives: list[dict], placements: dict[str, list[str]],
                 budget_usd: float | None) -> dict:
    budget = budget_usd or DEFAULT_BUDGET_USD
    by_id = {p.id: p for p in pubs}
    recommended = [(s, by_id[s.publisher_id]) for s in pub_scores if s.status == "recommended"]
    rows, unallocated = allocate(recommended, budget)
    selected = [s for s in persona_scores if s.selected]
    start = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)   # next Monday

    # which creatives run where: a creative follows its persona's placements
    for row in rows:
        row["creative_persona_ids"] = [pid for pid, pub_ids in placements.items() if row["publisher_id"] in pub_ids]

    return {
        "campaign": {
            "name": f"{profile.product.title()} - launch test",
            "objective": objective(profile),
            "status": "draft",
            "flight": {"start": start.isoformat(), "end": (start + timedelta(days=FLIGHT_DAYS)).isoformat(), "days": FLIGHT_DAYS},
        },
        "budget": {"total_usd": budget, "daily_usd": round(budget / FLIGHT_DAYS, 2), "pacing": "even",
                   "source": "advertiser input" if budget_usd else f"default (${DEFAULT_BUDGET_USD:,} test budget)",
                   "unallocated_usd": unallocated,
                   "note": ("Recommended publishers cannot absorb the full budget at the assumed share of voice; "
                            "add publishers or extend the flight.") if unallocated else None},
        "targeting": {
            "personas": [{"id": s.persona_id, "name": s.name} for s in selected],
            "age_range": f"{profile.target_age_min}-{profile.target_age_max}",
            "gender": profile.target_gender,
            "income_tiers": sorted({pub.audience.income_tier for _, pub in recommended}),
            "geos": sorted({g for _, pub in recommended for g in pub.audience.top_geos}),
            "interests": profile.persona_affinities + profile.values,
            "contextual_categories": sorted({pub.category for _, pub in recommended}),
            "excluded_publishers": [{"id": s.publisher_id, "name": s.name, "reason": s.exclusion_reason}
                                    for s in pub_scores if s.status == "excluded"],
        },
        "publishers": rows,
        "bid_strategy": bid_strategy(profile, rows) if rows else None,
        "frequency_cap": FREQUENCY_CAP,
        "creatives": creatives,
        "assumptions": [
            f"Estimated CPMs come from income tier (${INCOME_CPM_USD['mid']}-${INCOME_CPM_USD['high']}) times a category multiplier; no real rate cards in the catalog.",
            f"Reach cap assumes we win at most {SHARE_OF_VOICE:.0%} of a publisher's monthly impressions; capped money moves to the next-best publisher.",
            f"Funnel estimates assume {ASSUMED_CTR:.1%} CTR and {ASSUMED_CVR:.0%} conversion rate.",
            f"Target CPA is {TARGET_CPA_SHARE:.0%} of first-order value" + (f", times {SUBSCRIPTION_LTV_MULT:.0f} for subscriptions." if profile.business_model == "subscription" else "."),
            *profile.assumptions,
        ],
    }
