"""
Orchestrates the whole plan. Read this file top to bottom to understand the system.

    1. extract    advertiser text -> AdvertiserProfile            (LLM)
    2. retrieve   hard filters, lexical + semantic rankers, RRF    (code)
    3. rerank     LLM re-scores the shortlist and writes reasons   (LLM)
    4. create     one creative per selected persona                (LLM)
    5. configure  budget split, bids, targeting                    (code)

deterministic_only=True skips the two LLM stages after extraction, which is handy for
evals of the ranker and for showing the fusion order next to the reranked order.
"""
import time
from typing import Optional

from .campaign import build_config
from .catalog import personas, publishers
from .creatives import generate_creatives
from .embeddings import catalog_vectors, embed_profile, placements
from .extract import extract_profile
from .rerank import RECOMMEND_FLOOR, rerank, use_reranker
from .retrieval import score_personas, score_publishers


def run_plan(description: str, budget_usd: Optional[float] = None, deterministic_only: bool = False) -> dict:
    trace: list[dict] = []

    def step(name: str, started: float, **extra) -> None:
        trace.append({"step": name, "ms": round((time.time() - started) * 1000), **extra})

    # 1. understand the advertiser
    t = time.time()
    profile, extract_prompt = extract_profile(description)
    step("extract_profile", t, prompt=extract_prompt)
    if profile.clarity == "none":
        return {"status": "needs_input", "profile": profile.model_dump(), "trace": trace}

    # 2. rank everything deterministically
    t = time.time()
    vectors = catalog_vectors()
    query_vec = embed_profile(profile)
    pub_scores = score_publishers(profile, publishers(), query_vec, vectors)
    persona_scores = score_personas(profile, personas(), query_vec, vectors)
    step("retrieve", t)
    if all(s.status == "excluded" for s in pub_scores):
        return {"status": "no_fit", "profile": profile.model_dump(),
                "publishers": [s.model_dump() for s in pub_scores],
                "personas": [s.model_dump() for s in persona_scores], "trace": trace}

    # 3. LLM judgment over the shortlist
    t = time.time()
    pub_scores, placement_note, rerank_prompt = rerank(profile, pub_scores, enabled=not deterministic_only)
    step("rerank", t, prompt=rerank_prompt, skipped=deterministic_only or not use_reranker())

    # who runs where: each selected persona follows the recommended publishers it best matches
    selected = [s for s in persona_scores if s.selected]
    recommended_ids = [s.publisher_id for s in pub_scores if s.status == "recommended"]
    place = placements([s.persona_id for s in selected], recommended_ids)

    # 4. creatives, one per selected persona
    t = time.time()
    creatives, warnings, creative_prompt = [], [], ""
    if not deterministic_only:
        by_id = {p.id: p for p in publishers()}
        creatives, warnings, creative_prompt = generate_creatives(
            profile, selected, {pid: [by_id[i] for i in ids] for pid, ids in place.items()})
    step("creatives", t, prompt=creative_prompt, skipped=deterministic_only)

    # 5. the campaign config a downstream system could run
    t = time.time()
    creative_dicts = [c.model_dump() for c in creatives]
    config = build_config(profile, pub_scores, publishers(), persona_scores, creative_dicts, place, budget_usd)
    step("campaign_config", t)

    return {
        "status": "ok",
        "profile": profile.model_dump(),
        "publishers": [s.model_dump() for s in pub_scores],
        "placement_note": placement_note,
        "personas": [s.model_dump() for s in persona_scores],
        "placements": place,
        "creatives": creative_dicts,
        "creative_warnings": warnings,
        "config": config,
        "thresholds": {"recommend_floor": RECOMMEND_FLOOR},
        "trace": trace,
    }
