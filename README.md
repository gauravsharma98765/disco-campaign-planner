# Campaign Planner

You type one or two sentences about what you sell. The app tells you which publishers to advertise on and why, which ones it dropped and why, writes one ad for each shopper persona it thinks fits, and gives back a campaign config with the budget split, bid strategy and targeting. Every ranking shows the numbers behind it.

**Live demo:** _Railway URL here_. **Run it locally** (Python 3.12):

```bash
cp .env.example .env                        # add EDEN_API_KEY
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload     # http://localhost:8000  (?example=1 auto-runs a sample)
.venv/bin/python -m pytest -q               # 11 unit tests, no LLM
.venv/bin/python -m eval.run_examples && .venv/bin/python -m eval.metrics   # 15 samples, precision@3
```

## What I built

Five steps, in `app/pipeline.py`. Three call an LLM (gpt-4.1-mini through Eden AI, prompts in `prompts/`), two are plain Python.

**1. Extraction (LLM).** The sentence goes to the model with a fixed JSON schema and the catalog's own tag vocabulary. Back comes a structured profile: category, the tag that names the product type, price tier, estimated order value, audience, values, plus a clarity level, the assumptions it made and the questions it would ask. Any tag not in the catalog is dropped in code. Messy input is handled here: "idk just try it" stops and asks, a vague pitch runs with its assumptions listed, B2B ends in an explained no-fit.

**2. Retrieval (code).** A retrieve-then-rank setup, just on a 20-item catalog. Hard filters first: B2B, a price more than 15x the publisher's average basket, a gender mismatch over 90%. Each excluded publisher keeps the rule that removed it. Then a **lexical ranker**: eight hand-weighted features (category match, product-type match, tag overlap, gender, age, income, price vs AOV, keyword overlap with the publisher's notes). It is a linear model, so every feature's contribution is shown as a bar. Then a **semantic ranker**: cosine similarity between the embedding of the profile and cached embeddings of each publisher's description. **Reciprocal Rank Fusion** merges the two. Personas go through the same two rankers, with a penalty when the pitch trips one of the persona's disinterests.

**3. Reranking (LLM).** The top 8 fused candidates go to the model with the profile and each publisher's card. It returns a fit score from 0 to 100 on a fixed rubric plus a one-line rationale each. It can reorder the shortlist but never sees excluded publishers, so it cannot bring one back.

**4. Creatives (LLM).** One call writes a headline, body and CTA per selected persona, and says which persona insight it used and what it avoided. A code check flags disinterest phrases, length breaches and near-duplicate copy.

**5. Campaign config (code).** Budget split in proportion to fit score, capped by each publisher's reach. Bid strategy by business model: target CPA for subscriptions, CPM for luxury, CPC otherwise. Targeting from the chosen personas and publishers. Each creative mapped to the publishers its persona shops on. Every constant I made up is listed under `assumptions`.

**How I checked it.** I hand-labelled the 11 clear samples with the publishers a planner would pick. Precision@3 is 0.82 with the reranker and 0.67 with fusion alone, so the LLM stage earns its place. The B2B and nonsense inputs land on the statuses I expected. LLM responses are cached by prompt hash, so the sample chips load instantly and give the same answer every time.

## What I would do with another week

Get click and conversion data and fit the lexical weights instead of setting them by hand. The model is already a weighted sum, so they just become learned coefficients. Check the reranker's calibration across a few models, then try a fine-tuned cross-encoder for the scoring: milliseconds instead of seconds. Fix persona matching, the weakest part; embeddings do not do well there. Pull real rate cards for CPMs and reach. Stream each stage to the UI and run the independent LLM calls in parallel.

## What I cut and why

Image creatives: the brief asked for text. Real CPMs and inventory: none in the data, so I used labelled constants. Login and saving: nothing to save yet. Asking the advertiser follow-up questions: I show the questions instead, so it stays one request in, one plan out. One creative call per persona: a single call is faster and the variants come out more distinct. A learned ranker: no training data.

## Hard vs easy

Easy: the UI, the JSON plumbing, getting a model to write decent copy. Hard: extraction, because everything downstream depends on it and most bad rankings I fixed by adding a field, not tuning a weight. Calibration, because without a rubric "fit 70" meant different things on different runs, and a small model still over-scores impulse publishers. Persona matching, where tags and generic words like "premium" fail. Evaluation without outcome data, so the numbers above are directional. The interesting work is the feature design and the eval loop, and in production it is swapping my labels and hand-set weights for conversions and fitted coefficients.
