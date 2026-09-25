"""
Step 1: advertiser one-liner -> AdvertiserProfile.

One LLM call constrained to a Pydantic JSON schema. After the call we normalise the
controlled-vocabulary fields against the real catalog, so a hallucinated tag can
never reach the scorer.
"""
from .catalog import vocabulary
from .llm import generate_json, load_prompt
from .schemas import AdvertiserProfile


def build_prompt(description: str) -> str:
    vocab = vocabulary()
    return load_prompt(
        "extract_profile",
        description=description.strip(),
        categories=", ".join(vocab["categories"]),
        subcategories=", ".join(vocab["subcategories"]),
        persona_affinities=", ".join(vocab["persona_affinities"]),
    )


def normalise(profile: AdvertiserProfile) -> AdvertiserProfile:
    """Drop any vocabulary values the model invented; keep order, dedupe."""
    vocab = vocabulary()

    def keep(values: list[str], allowed: list[str]) -> list[str]:
        seen, out = set(), []
        for v in values:
            v = v.strip().lower().replace(" ", "_")
            if v in allowed and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    profile.catalog_categories = keep(profile.catalog_categories, vocab["categories"])
    profile.catalog_subcategories = keep(profile.catalog_subcategories, vocab["subcategories"])
    profile.persona_affinities = keep(profile.persona_affinities, vocab["persona_affinities"])
    primary = profile.primary_subcategory.strip().lower().replace(" ", "_")
    profile.primary_subcategory = primary if primary in vocab["subcategories"] else ""
    if profile.primary_subcategory and profile.primary_subcategory not in profile.catalog_subcategories:
        profile.catalog_subcategories.insert(0, profile.primary_subcategory)
    profile.values = [v.strip().lower() for v in profile.values if v.strip()]
    return profile


def extract_profile(description: str) -> tuple[AdvertiserProfile, str]:
    """Returns the profile and the exact prompt used (for the trace panel)."""
    prompt = build_prompt(description)
    profile = generate_json(prompt, AdvertiserProfile, temperature=0.1)
    return normalise(profile), prompt
