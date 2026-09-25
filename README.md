# Campaign Planner

An advertiser types a sentence or two. The system returns ranked publishers with reasons and explicit exclusions, one ad creative per plausible shopper persona, and a campaign config a downstream system could run. Every score is shown with the features that produced it.

**Live demo:** _Railway URL here_ · **Local** (Python 3.12):

```bash
cp .env.example .env                        # add EDEN_API_KEY (Eden AI, OpenAI-compatible)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload     # http://localhost:8000  (?example=1 auto-runs a sample)
.venv/bin/python -m pytest -q               # 11 unit tests, no LLM
.venv/bin/python -m eval.run_examples && .venv/bin/python -m eval.metrics   # 15 samples, precision@3
```

## What I built

Five stages in `app/pipeline.py`: three LLM calls (`openai/gpt-4.1-mini` via Eden AI, prompts in `prompts/`) and two of plain code.

1. **Extract** (LLM) → `AdvertiserProfile`: catalog tags, the tag naming the product type, price tier and order value, business model, audience, values, plus clarity, assumptions and clarifying questions. Tags are normalised against the catalog vocabulary, so a hallucinated tag never reaches the scorer. Messy input is handled here, once: "idk just try it" stops with questions, "we help people feel better" proceeds on stated assumptions, B2B ends in an explained no-fit.
2. **Retrieve** (code, `app/retrieval.py`): hard filters that record the rule that fired → lexical ranker (8 weighted features: category, product type, tags, gender, age, income, price vs AOV, publisher-note keywords) → semantic ranker (embedding cosine) → Reciprocal Rank Fusion. Personas use the same rankers, with tripped `disinterested_in` phrases as a penalty; demographics alone never select a persona.
3. **Rerank** (LLM): scores the top 8 against a banded rubric and writes an analyst rationale each. It can reorder candidates, never resurrect an excluded one.
4. **Create** (LLM, one call): one variant per selected persona with its insight and what it avoided. Deterministic QA flags disinterest phrases, length breaches and near-duplicates.
5. **Configure** (code, `app/campaign.py`): budget proportional to fit, water-filled against reach caps; objective and bid strategy by business model (subscription → target CPA, luxury → CPM reach then retarget, else CPC with a CPA guardrail); targeting from the persona × publisher intersection; creatives mapped to their persona's publishers. Every constant is echoed into `assumptions`.

**Eval.** On 11 clear-cut samples (`eval/labels.json`) precision@3 is 0.82 with the reranker, 0.67 with fusion alone; the B2B and no-signal cases hit the expected statuses. Responses are cached by prompt hash, so the sample chips are instant and reproducible.

## Another week

Fit the lexical weights from click and conversion data and retire my labels; the score is already a weighted sum with visible contributions. Calibrate the reranker across models, then try a fine-tuned cross-encoder for cost at scale. LLM-judge persona fit, the weakest signal. Pull rate cards for real reach caps and bids. Stream stages to the UI and run independent LLM calls concurrently.

## Cut, and why

Image creatives: text was the ask. Real CPMs and inventory: none in the pack, so constants are labelled. Persistence and auth: nothing to persist. Multi-turn refinement: questions are shown, not asked, to keep one request in and one plan out. Per-persona creative calls: one call is faster and keeps variants distinct. A learned ranker: no training data.

## Hard vs easy

Easy: the UI, JSON plumbing, plausible copy. Hard: the intermediate representation, since extraction bounds everything and most bad rankings were fixed by a field, not a weight; calibration, since a rubric was needed to make "fit 70" mean the same across runs and a small model still over-scores impulse publishers; persona matching, where hand-written tags and words like "premium" fail worst; and evaluation with no outcome data. The interesting engineering is the feature design and the eval loop, and in production, replacing hand-set weights and my labels with fitted coefficients and conversions.
