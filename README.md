# Campaign Planner

You type a sentence or two about what you sell. The app tells you which publishers to advertise on and why, which ones it dropped and why, writes one ad per shopper persona that fits, and returns a campaign config with budget split, bids and targeting. Every ranking shows the numbers behind it.

**Live demo:** https://disco-campaign-planner-production.up.railway.app/

**Run locally** (Python 3.12): copy `.env.example` to `.env` and add your `EDEN_API_KEY`, then

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload          # http://localhost:8000
.venv/bin/python -m pytest -q                    # unit tests, no LLM
.venv/bin/python -m eval.run_examples && .venv/bin/python -m eval.metrics
```

## What I built

Five steps in `app/pipeline.py`. Three call an LLM (gpt-4.1-mini via Eden AI, prompts in `prompts/`), two are plain Python.

**1. Extraction (LLM).** The sentence goes to the model with a JSON schema and the catalog's own tag vocabulary. Back comes a structured profile: category, the tag naming the product type, price tier, order value, audience, values, anything the advertiser said must not be claimed, plus clarity, assumptions and open questions. Tags not in the catalog are dropped in code. Messy input is handled here: nonsense stops and asks, a vague pitch runs with its assumptions listed, B2B ends in an explained no-fit.

**2. Retrieval (code).** Retrieve-then-rank on a 20-item catalog. **Hard filters** first (B2B, price over 15x the publisher's basket, gender mismatch over 90%), each keeping the rule that fired. Then a **lexical ranker**: eight hand-weighted features (category, product type, tag overlap, gender, age, income, price vs AOV, note keywords), a linear model whose per-feature contributions are shown as bars. Then a **semantic ranker**: cosine similarity between the profile embedding and cached publisher embeddings. **Reciprocal Rank Fusion** merges the two. Personas use the same rankers, with a penalty when the pitch trips a disinterest.

**3. Reranking (LLM).** The top 8 fused candidates go to the model with the profile and each publisher's card. It returns a 0 to 100 fit score on a fixed rubric plus a one-line rationale, and never sees excluded publishers.

**4. Creatives (LLM).** One call writes headline, body and CTA per selected persona, naming the insight used and what it avoided. A code check flags unsupported claims (offers, endorsements, proof, promises the advertiser never stated), disinterest phrases, length breaches and near-duplicates; if a claim was invented, the model gets one rewrite pass.

**5. Campaign config (code).** Budget split by fit score, capped by each publisher's reach. Bid strategy by business model: target CPA for subscriptions, CPM for luxury, CPC otherwise, plus a feasibility check that compares what a click costs with what a click can be worth and says when the economics do not close. Every constant I made up is listed under `assumptions`.

**How I checked it.** I hand-labelled the 11 clear samples. Precision@3 is 0.82 with the reranker and 0.67 with fusion alone, so the LLM stage earns its place. The B2B, nonsense and "no discounts" inputs land where expected. Responses are cached by prompt hash, so the sample chips are instant and repeatable.

## What I would do with another week

Fit the lexical weights from click and conversion data instead of by hand; the model is already a weighted sum. Check the reranker's calibration across models, then try a fine-tuned cross-encoder for the scoring. Fix persona matching, the weakest part. Replace the funnel constants with real rate cards and measured rates. Stream stages to the UI.

## What I cut and why

Image creatives: the brief asked for text. Real CPMs: none in the data, so constants are labelled. Login and saving: nothing to save. Follow-up questions: shown, not asked, so it stays one request in, one plan out. Per-persona creative calls: one call is faster and the variants come out more distinct. A learned ranker: no training data.

## Hard vs easy

Easy: the UI, the JSON plumbing, getting a model to write plausible copy. Hard: extraction, because everything downstream depends on it and most bad rankings were fixed by adding a field, not tuning a weight. Keeping copy honest, because a persona's "discount-forward" preference reads to the model as permission to invent a discount. Calibration, because without a rubric "fit 70" meant different things on different runs. Persona matching, where tags and generic words like "premium" fail. Evaluation without outcome data, so the numbers above are directional. The interesting work is the feature design and the eval loop, and in production, swapping my labels and hand-set weights for conversions and fitted coefficients.
