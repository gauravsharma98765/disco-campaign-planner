"""
Step 4: one creative per selected persona, in a single LLM call.

One call (not one per persona) is cheaper and faster, and lets the model
see all variants at once, which is how it keeps them distinct. A deterministic check
afterwards flags copy that trips a persona's disinterests or breaks length limits.
"""
import json

from pydantic import BaseModel, ConfigDict, Field

from .catalog import personas
from .llm import generate_json, load_prompt
from .retrieval import phrase_conflicts, tokens
from .schemas import AdvertiserProfile, PersonaScore, Publisher

MAX_HEADLINE, MAX_BODY = 60, 160


class Creative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: str
    headline: str = Field(description="At most 60 characters.")
    body: str = Field(description="At most 160 characters.")
    cta: str = Field(description="Call to action, 2-4 words.")
    angle: str = Field(description="One sentence: the persona insight this copy is built on.")
    leaned_into: list[str] = Field(description="Persona messaging preferences this copy uses.")
    avoided: list[str] = Field(description="Persona disinterests this copy deliberately avoids.")


class CreativeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creatives: list[Creative]


def personas_text(selected: list[PersonaScore], placements: dict[str, list[Publisher]]) -> str:
    by_id = {p.id: p for p in personas()}
    blocks = []
    for s in selected:
        p = by_id[s.persona_id]
        where = ", ".join(f"{pub.name} ({pub.category.replace('_', ' ')})" for pub in placements.get(p.id, [])) or "general placements"
        blocks.append(
            f"[{p.id}] {p.name} (ages {p.age_range}, {p.gender_skew})\n"
            f"    who: {p.description}\n"
            f"    messaging_preferences: {', '.join(p.messaging_preferences)}\n"
            f"    disinterested_in: {', '.join(p.disinterested_in)}\n"
            f"    price_sensitivity: {p.price_sensitivity}\n"
            f"    why selected: {s.why}\n"
            f"    runs on: {where}"
        )
    return "\n\n".join(blocks)


def check(creatives: list[Creative]) -> list[dict]:
    """Deterministic QA. Returns a list of {persona_id, warning} rows; empty means clean."""
    by_id = {p.id: p for p in personas()}
    warnings = []
    for c in creatives:
        copy_words = tokens(f"{c.headline} {c.body}")
        persona = by_id.get(c.persona_id)
        if persona:
            for phrase in phrase_conflicts(persona.disinterested_in, copy_words):
                warnings.append({"persona_id": c.persona_id, "warning": f"copy contains a disinterest phrase: '{phrase}'"})
        if len(c.headline) > MAX_HEADLINE:
            warnings.append({"persona_id": c.persona_id, "warning": f"headline is {len(c.headline)} chars (max {MAX_HEADLINE})"})
        if len(c.body) > MAX_BODY:
            warnings.append({"persona_id": c.persona_id, "warning": f"body is {len(c.body)} chars (max {MAX_BODY})"})
    # Variants that share most of their words are not really variants.
    for i, a in enumerate(creatives):
        for b in creatives[i + 1:]:
            wa, wb = tokens(a.body), tokens(b.body)
            if wa and wb and len(wa & wb) / len(wa | wb) > 0.5:
                warnings.append({"persona_id": b.persona_id, "warning": f"body copy overlaps heavily with {a.persona_id}"})
    return warnings


def generate_creatives(profile: AdvertiserProfile, selected: list[PersonaScore],
                       placements: dict[str, list[Publisher]]) -> tuple[list[Creative], list[dict], str]:
    """Returns (creatives, qa_warnings, prompt_used)."""
    prompt = load_prompt("generate_creatives",
                         profile=json.dumps(profile.model_dump(), indent=1),
                         personas=personas_text(selected, placements))
    result = generate_json(prompt, CreativeSet, temperature=0.8)
    wanted = [s.persona_id for s in selected]
    creatives = sorted((c for c in result.creatives if c.persona_id in wanted), key=lambda c: wanted.index(c.persona_id))
    return creatives, check(creatives), prompt
