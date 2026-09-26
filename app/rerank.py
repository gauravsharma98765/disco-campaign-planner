"""
LLM pass over the top of the fused list: a fit score and a one-line reason per publisher. It only
sees the shortlist, so it can reorder but can't bring back anything a filter removed.
USE_RERANKER=0 falls back to the fusion order.
"""
import json
import os

from pydantic import BaseModel, ConfigDict, Field

from .catalog import publisher_doc, publishers
from .llm import generate_json, load_prompt
from .schemas import AdvertiserProfile, PublisherScore

SHORTLIST_SIZE = 8
RECOMMEND_FLOOR = 65     # fit_score needed to be "recommended" beyond the guaranteed minimum
MIN_RECOMMENDED, MAX_RECOMMENDED = 3, 6


class RerankItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publisher_id: str
    fit_score: int = Field(description="0-100 likelihood this publisher's shoppers buy the product.")
    rationale: str = Field(description="One or two analyst sentences citing concrete facts.")


class RerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[RerankItem]
    overall_note: str


def use_reranker() -> bool:
    return os.environ.get("USE_RERANKER", "1") != "0"


def shortlist_text(shortlist: list[PublisherScore]) -> str:
    by_id = {p.id: p for p in publishers()}
    lines = []
    for s in shortlist:
        pub = by_id[s.publisher_id]
        evidence = "; ".join(f"{f.name}={f.value:.2f} ({f.note})" for f in s.lexical_features if f.value > 0)
        lines.append(f"[{s.publisher_id}] rank {s.fused_rank}. {publisher_doc(pub)}\n"
                     f"    ranker evidence: {evidence}\n    semantic similarity: {s.semantic_score:.2f}")
    return "\n".join(lines)


def deterministic_rationale(s: PublisherScore, total: int) -> str:
    weakest = min(s.lexical_features, key=lambda f: f.value) if s.lexical_features else None
    why = f" Weakest signal: {weakest.name.replace('_', ' ')} ({weakest.note})." if weakest else ""
    return (f"Ranked {s.fused_rank} of {total} candidates after fusion "
            f"(lexical #{s.lexical_rank}, semantic #{s.semantic_rank}).{why}")


def rerank(profile: AdvertiserProfile, scores: list[PublisherScore], enabled: bool = True) -> tuple[list[PublisherScore], str, str]:
    """Fills fit_score / rationale / status on the score objects.
    Returns (scores, overall_note, prompt_used). Excluded publishers are untouched."""
    candidates = [s for s in scores if s.status != "excluded"]
    shortlist = candidates[:SHORTLIST_SIZE]
    prompt, note = "", ""

    if enabled and use_reranker() and shortlist:
        prompt = load_prompt("rerank_publishers",
                             profile=json.dumps(profile.model_dump(), indent=1),
                             shortlist=shortlist_text(shortlist))
        result = generate_json(prompt, RerankResult, temperature=0.2)
        by_id = {item.publisher_id: item for item in result.items}
        for s in shortlist:
            item = by_id.get(s.publisher_id)
            if item:
                s.fit_score, s.rationale = max(0, min(100, item.fit_score)), item.rationale
        note = result.overall_note
    # Anything the LLM did not score (reranker off, or a missing id) gets a fused-order score.
    for s in shortlist:
        if s.fit_score is None:
            s.fit_score = round(100 * (1 - (s.fused_rank - 1) / max(1, len(candidates))))
    for s in candidates:
        if s.rationale is None:
            s.rationale = deterministic_rationale(s, len(candidates))

    # Recommend: best fit first, at least MIN, at most MAX, beyond MIN only if above the floor.
    shortlist.sort(key=lambda s: (-(s.fit_score or 0), s.fused_rank or 999))
    for i, s in enumerate(shortlist):
        s.status = "recommended" if (i < MIN_RECOMMENDED or (i < MAX_RECOMMENDED and s.fit_score >= RECOMMEND_FLOOR)) else "considered"

    order = {"recommended": 0, "considered": 1, "excluded": 2}
    scores.sort(key=lambda s: (order[s.status], -(s.fit_score or -1), s.fused_rank or 999))
    return scores, note, prompt
