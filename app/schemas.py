"""
All data shapes in one place.

Three groups:
  1. Catalog models   - typed views of data/publishers.json and data/shopper_personas.json
  2. AdvertiserProfile - what the LLM extracts from the advertiser's one-liner (step 1)
  3. Scoring models   - what the ranking pipeline produces, with every number it used
"""
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


# --- 1. Catalog -------------------------------------------------------------

class Audience(BaseModel):
    age_skew: str                      # e.g. "25-44" or "nationwide" for geos
    gender_split: dict[str, float]     # {"female": 0.62, "male": 0.37, "other": 0.01}
    top_geos: list[str]
    income_tier: str                   # "mid" | "mid-high" | "high"


class Publisher(BaseModel):
    id: str
    name: str
    category: str
    subcategories: list[str]
    monthly_impressions: int
    avg_order_value_usd: float
    audience: Audience
    notes: str


class Persona(BaseModel):
    id: str
    name: str
    age_range: str
    gender_skew: str                   # "female" | "balanced" | "female-leaning"
    description: str
    category_affinities: list[str]
    price_sensitivity: str             # "low" | "low-medium" | "medium" | "medium-high" | "high"
    messaging_preferences: list[str]
    disinterested_in: list[str]
    typical_aov_usd: float


# --- 2. Advertiser profile (LLM output, step 1) -----------------------------

Clarity = Literal["high", "medium", "low", "none"]
PriceTier = Literal["budget", "mid", "premium", "luxury"]
BusinessModel = Literal["one_time", "subscription", "gifting", "b2b", "unknown"]
Gender = Literal["female", "male", "balanced", "unknown"]


class AdvertiserProfile(BaseModel):
    """Structured reading of the advertiser's description. Everything downstream
    reads from this object, never from the raw text, so messy input is handled once."""
    model_config = ConfigDict(extra="forbid")   # lets the LLM API enforce the schema strictly
    clarity: Clarity = Field(description="How much usable signal the description carried.")
    is_consumer_commerce: bool = Field(description="True if individual consumers buy this product/service online. False for B2B, enterprise, services sold to businesses.")
    summary: str = Field(description="One neutral sentence: what is sold, to whom, and the positioning. Used for semantic matching.")
    product: str = Field(description="Short product noun phrase, e.g. 'grain-free senior dog food'.")
    catalog_categories: list[str] = Field(description="Publisher categories from the provided vocabulary that fit this advertiser. Empty if none fit.")
    catalog_subcategories: list[str] = Field(description="Publisher subcategories from the provided vocabulary that fit. Empty if none fit.")
    primary_subcategory: str = Field(description="The single subcategory from the vocabulary that names the product type itself (e.g. 'pet_food', 'activewear', 'bedding'), not an attribute like 'sustainable' or 'women'. Empty string if none names it.")
    persona_affinities: list[str] = Field(description="Persona affinity tags from the provided vocabulary that fit.")
    price_tier: PriceTier
    estimated_price_usd: float = Field(description="Typical first order in USD. Estimate from category and tier when not stated; 0 only when clarity is none.")
    business_model: BusinessModel
    target_gender: Gender
    target_age_min: int = Field(description="Youngest plausible buyer age. Always give a range; widen it when unsure.")
    target_age_max: int = Field(description="Oldest plausible buyer age.")
    values: list[str] = Field(description="Brand values / claims in the pitch, e.g. 'sustainability', 'vet-formulated', 'craftsmanship'.")
    tone: str = Field(description="Brand voice in 2-4 words, e.g. 'premium, understated'.")
    assumptions: list[str] = Field(description="Things you had to assume because the description did not say. Empty if clarity is high.")
    clarifying_questions: list[str] = Field(description="Questions that would most improve the plan. Empty if clarity is high.")


# --- 3. Scoring outputs -----------------------------------------------------

class Feature(BaseModel):
    """One term of the lexical score: value in [0,1], weight, and their product."""
    name: str
    value: float
    weight: float
    contribution: float
    note: str = ""                     # human-readable evidence, e.g. "pet_food, subscription"


class PublisherScore(BaseModel):
    publisher_id: str
    name: str
    status: Literal["recommended", "considered", "excluded"] = "considered"
    exclusion_reason: Optional[str] = None
    # ranker A: lexical / structured
    lexical_score: float = 0.0
    lexical_rank: Optional[int] = None
    lexical_features: list[Feature] = []
    # ranker B: semantic
    semantic_score: float = 0.0
    semantic_rank: Optional[int] = None
    # fusion
    fused_score: float = 0.0
    fused_rank: Optional[int] = None
    # reranker (LLM), filled in step 3
    fit_score: Optional[int] = None
    rationale: Optional[str] = None


class PersonaScore(BaseModel):
    persona_id: str
    name: str
    selected: bool = False
    lexical_score: float = 0.0
    lexical_rank: Optional[int] = None
    lexical_features: list[Feature] = []
    semantic_score: float = 0.0
    semantic_rank: Optional[int] = None
    fused_score: float = 0.0
    fused_rank: Optional[int] = None
    why: str = ""                      # deterministic one-liner built from features
