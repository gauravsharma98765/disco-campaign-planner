# Campaign Planner

You type a sentence or two about what you sell. The app tells you which publishers to advertise on and why, which ones it dropped and why, writes one ad per shopper persona that fits, and returns a campaign config with budget split, bids and targeting. Every ranking shows the numbers behind it.

**Live demo:** https://disco-campaign-planner-production.up.railway.app/ 

## What I built

Five steps in `app/pipeline.py`. Three call an LLM (gpt-4.1-mini via Eden AI, prompts in `prompts/`), two are plain Python.

**1. Extraction (LLM).** The sentence goes to the model with a JSON schema and the catalog's own tag vocabulary. Back comes a structured profile: category, the tag naming the product type, price tier, order value, audience, values, plus a clarity level, assumptions and the questions it would ask. Tags not in the catalog are dropped in code. Messy input is handled here: "idk just try it" stops and asks, a vague pitch runs with its assumptions listed, B2B ends in an explained no-fit.

**2. Retrieval (code).** Retrieve-then-rank on a 20-item catalog. **Hard filters** first: B2B, a price over 15x the publisher's average basket, a gender mismatch over 90%; each excluded publisher keeps the rule that removed it. Then a **lexical ranker**: eight hand-weighted features (category, product type, tag overlap, gender, age, income, price vs AOV, keywords in the publisher's notes). It is a linear model, so each feature's contribution is shown as a bar. Then a **semantic ranker**: cosine similarity between the profile embedding and cached publisher embeddings. **Reciprocal Rank Fusion** merges the two. Personas go through the same rankers, with a penalty when the pitch trips a persona's disinterests.

**3. Reranking (LLM).** The top 8 fused candidates go to the model with the profile and each publisher's card. It returns a 0 to 100 fit score on a fixed rubric plus a one-line rationale. It can reorder the shortlist but never sees excluded publishers.

**4. Creatives (LLM).** One call writes headline, body and CTA per selected persona, naming the insight used and what it avoided. A code check flags disinterest phrases, length breaches and near-duplicates.

**5. Campaign config (code).** Budget split by fit score, capped by each publisher's reach. Bid strategy by business model: target CPA for subscriptions, CPM for luxury, CPC otherwise. Targeting from the chosen personas and publishers, creatives mapped to where their persona shops. Every constant I made up is listed under `assumptions`.

**How I checked it.** I hand-labelled the 11 clear samples. Precision@3 is 0.82 with the reranker and 0.67 with fusion alone, so the LLM stage earns its place. The B2B and nonsense inputs land where expected. Responses are cached by prompt hash, so the sample chips are instant and repeatable.

## What I would do with another week

Fit the lexical weights from click and conversion data instead of by hand; the model is already a weighted sum. Check the reranker's calibration across models, then try a fine-tuned cross-encoder for the scoring, milliseconds instead of seconds. Fix persona matching, the weakest part. Pull real rate cards for CPMs and reach. Stream stages to the UI and run independent LLM calls in parallel.

## What I cut and why

Image creatives: the brief asked for text. Real CPMs: none in the data, so constants are labelled. Login and saving: nothing to save. Follow-up questions to the advertiser: shown, not asked, to keep one request in, one plan out. One creative call per persona: a single call is faster and the variants come out more distinct. A learned ranker: no training data.

## Hard vs easy

Easy: the UI, the JSON plumbing, getting a model to write decent copy. Hard: extraction, because everything downstream depends on it and most bad rankings were fixed by adding a field, not tuning a weight. Calibration, because without a rubric "fit 70" meant different things on different runs, and a small model still over-scores impulse publishers. Persona matching, where tags and generic words like "premium" fail. Evaluation without outcome data, so the numbers above are directional. The interesting work is the feature design and the eval loop, and in production, swapping my labels and hand-set weights for conversions and fitted coefficients.
