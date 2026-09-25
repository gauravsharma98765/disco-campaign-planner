"""
Semantic side of matching.

Catalog embeddings (20 publishers + 10 personas) are computed once and cached to
data/embeddings_cache.json, which is committed, so a fresh deploy never has to
re-embed and results are reproducible. Only the advertiser profile is embedded
per request. The cache invalidates itself if a catalog text or the model changes.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import llm
from .catalog import DATA_DIR, persona_doc, personas, publisher_doc, publishers
from .schemas import AdvertiserProfile

CACHE_PATH: Path = DATA_DIR / "embeddings_cache.json"


def _catalog_docs() -> dict[str, str]:
    docs = {p.id: publisher_doc(p) for p in publishers()}
    docs.update({p.id: persona_doc(p) for p in personas()})
    return docs


@lru_cache(maxsize=1)
def catalog_vectors() -> dict[str, np.ndarray]:
    """id -> unit-normalised embedding for every publisher and persona."""
    docs = _catalog_docs()
    cached = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    # Rebuild if any doc text changed (so editing the catalog invalidates the cache).
    stale = [i for i, text in docs.items()
             if cached.get(i, {}).get("text") != text or cached.get(i, {}).get("model") != llm.EMBED_MODEL]
    if stale:
        vectors = llm.embed([docs[i] for i in stale])
        for i, vec in zip(stale, vectors):
            cached[i] = {"text": docs[i], "model": llm.EMBED_MODEL, "vector": vec}
        CACHE_PATH.write_text(json.dumps(cached))
    return {i: _unit(np.array(v["vector"])) for i, v in cached.items() if i in docs}


def profile_text(profile: AdvertiserProfile) -> str:
    """What we embed for the advertiser: the summary plus the value tags."""
    values = ", ".join(profile.values) if profile.values else "none stated"
    return f"{profile.summary} Values: {values}. Price tier: {profile.price_tier}."


def embed_profile(profile: AdvertiserProfile) -> np.ndarray:
    return _unit(np.array(llm.embed([profile_text(profile)])[0]))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # both vectors are unit-normalised


def _unit(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v / norm if norm else v


def placements(persona_ids: list[str], publisher_ids: list[str], top_n: int = 2) -> dict[str, list[str]]:
    """Which recommended publishers each selected persona most plausibly shops on,
    by cosine similarity of the cached catalog embeddings. Creatives follow these placements."""
    vecs = catalog_vectors()
    return {pid: sorted(publisher_ids, key=lambda pub: -cosine(vecs[pid], vecs[pub]))[:top_n]
            for pid in persona_ids}
