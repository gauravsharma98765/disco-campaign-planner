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
from .retrieval import phrase_conflicts, profile_tokens, tokens
from .schemas import AdvertiserProfile, PersonaScore, Publisher

MAX_HEADLINE, MAX_BODY = 60, 160

# Words that assert an offer, endorsement, proof or promise. Copy may use a family only if the
# advertiser's own text uses one of its words; otherwise the copy is inventing it. (Stemmed forms,
# because tokens() strips plurals: "savings" -> "saving".)
CLAIM_FAMILIES = {
    "offer": {"discount", "deal", "bundle", "off"},   # not "save"/"saving": "save time", "time-saving" are not offers
    "endorsement": {"trusted", "recommended", "loved", "rated", "award", "winning"},
    "proof": {"certified", "clinically", "proven", "study", "studie", "tested", "backed"},
    "promise": {"guarantee", "ensure", "cure", "eliminate"},
}


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


def check(creatives: list[Creative], profile: AdvertiserProfile) -> list[dict]:
    """Deterministic QA. Returns a list of {persona_id, warning} rows; empty means clean."""
    by_id = {p.id: p for p in personas()}
    supported, restricted = profile_tokens(profile), tokens(" ".join(profile.restrictions))
    warnings = []
    for c in creatives:
        copy_words = tokens(f"{c.headline} {c.body}")
        for kind, family in CLAIM_FAMILIES.items():   # unsupported claim: family used, never stated by the advertiser
            hits = copy_words & family
            if hits and (family & restricted or not family & supported):
                warnings.append({"persona_id": c.persona_id, "warning": f"unsupported {kind} claim: {', '.join(sorted(hits))}"})
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
    wanted = [s.persona_id for s in selected]

    def ask(text: str) -> list[Creative]:
        result = generate_json(text, CreativeSet, temperature=0.8)
        return sorted((c for c in result.creatives if c.persona_id in wanted), key=lambda c: wanted.index(c.persona_id))

    creatives = ask(prompt)
    warnings = check(creatives, profile)
    invented = [w for w in warnings if w["warning"].startswith("unsupported")]
    if invented:   # one rewrite pass: show the model exactly what it invented
        prompt += ("\n\nYour previous attempt made claims the advertiser cannot support: "
                   + "; ".join(f"{w['persona_id']} -> {w['warning']}" for w in invented)
                   + ". Rewrite so no variant contains any offer, endorsement, proof or promise the profile does not state.")
        creatives = ask(prompt)
        warnings = check(creatives, profile)
    return creatives, warnings, prompt
