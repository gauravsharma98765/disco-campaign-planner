"""
Loads the data pack. Also turns each publisher and persona into a short paragraph, which is
what gets embedded and tokenised, so both rankers see the same text.
"""
import json
from functools import lru_cache
from pathlib import Path

from .schemas import Persona, Publisher

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def publishers() -> list[Publisher]:
    raw = json.loads((DATA_DIR / "publishers.json").read_text())
    return [Publisher(**p) for p in raw]


@lru_cache(maxsize=1)
def personas() -> list[Persona]:
    raw = json.loads((DATA_DIR / "shopper_personas.json").read_text())
    return [Persona(**p) for p in raw]


def publisher_doc(pub: Publisher) -> str:
    """Readable summary of a publisher, used for embedding and keyword overlap."""
    a = pub.audience
    female = round(a.gender_split.get("female", 0) * 100)
    return (
        f"{pub.name}: {pub.category.replace('_', ' ')} publisher "
        f"({', '.join(s.replace('_', ' ') for s in pub.subcategories)}). "
        f"Shoppers aged {a.age_skew}, {female}% female, {a.income_tier} income, "
        f"mostly {', '.join(a.top_geos)}. Average order ${pub.avg_order_value_usd:.0f}. "
        f"Notes: {pub.notes}"
    )


def persona_doc(p: Persona) -> str:
    """Readable summary of a persona, used for embedding and keyword overlap."""
    return (
        f"{p.name} (ages {p.age_range}, {p.gender_skew}). {p.description} "
        f"Interested in: {', '.join(x.replace('_', ' ') for x in p.category_affinities)}. "
        f"Responds to: {', '.join(p.messaging_preferences)}. "
        f"Price sensitivity: {p.price_sensitivity}."
        # disinterested_in is deliberately left out: embeddings read "not interested in X"
        # as being about X, so conflicts are handled lexically in retrieval.py instead.
    )


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, list[str]]:
    """Controlled vocabularies handed to the extraction prompt so the LLM maps the
    advertiser onto the catalog's own tags instead of inventing new ones."""
    cats = sorted({p.category for p in publishers()})
    subs = sorted({s for p in publishers() for s in p.subcategories})
    affinities = sorted({a for p in personas() for a in p.category_affinities})
    return {"categories": cats, "subcategories": subs, "persona_affinities": affinities}


def examples() -> list[dict]:
    """The sample advertiser one-liners from the data pack, for the UI chips and the eval."""
    import re
    out = []
    extra = DATA_DIR.parent / "eval" / "extra_examples.txt"      # regression cases we added ourselves
    files = [DATA_DIR / "example_advertisers.txt"] + ([extra] if extra.exists() else [])
    for path in files:
        for line in path.read_text().splitlines():
            m = re.match(r"^(\d+)\.\s+(.*)", line.strip())
            if m:
                out.append({"n": int(m.group(1)), "text": m.group(2)})
    return out
