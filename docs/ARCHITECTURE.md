# Architecture

One request in, one plan out. Solid boxes are code, the three shaded stages are LLM calls. Dotted lines are caches and the eval loop.

```mermaid
flowchart TD
    A([Advertiser sentence]) --> B

    subgraph S1[1. Extract - LLM]
        B[extract_profile<br/>prompts/extract_profile.md] --> C[normalise tags against<br/>the catalog vocabulary]
    end

    C --> D{clarity = none?}
    D -- yes --> E([needs_input<br/>clarifying questions])
    D -- no --> F

    subgraph S2[2. Retrieve - code]
        F[hard filters<br/>B2B, price vs basket, gender] --> G[lexical ranker<br/>8 weighted features]
        F --> H[semantic ranker<br/>embedding cosine]
        G --> I[Reciprocal Rank Fusion]
        H --> I
        I --> P[persona scoring<br/>and selection]
    end

    I --> J{any candidates left?}
    J -- no --> K([no_fit<br/>every exclusion reason])
    J -- yes --> L

    subgraph S3[3. Rerank - LLM]
        L[top 8 to fit score 0-100 + rationale<br/>prompts/rerank_publishers.md]
    end

    P --> M
    L --> M

    subgraph S4[4. Create - LLM]
        M[one creative per persona<br/>prompts/generate_creatives.md] --> N[claim, disinterest, length QA<br/>one rewrite pass if a claim was invented]
    end

    N --> O

    subgraph S5[5. Configure - code]
        O[budget water-fill against reach caps<br/>bid strategy + feasibility check<br/>targeting, creative placement]
    end

    O --> Q([campaign plan JSON])

    CACHE[(data/llm_cache.json<br/>keyed by prompt hash)] -.- B
    CACHE -.- L
    CACHE -.- M
    EMB[(data/embeddings_cache.json)] -.- H
    EVAL[eval/run_examples.py + eval/metrics.py<br/>precision@3 against hand labels] -.-> A

    style S1 fill:#eef2ff,stroke:#c7d2fe
    style S3 fill:#eef2ff,stroke:#c7d2fe
    style S4 fill:#eef2ff,stroke:#c7d2fe
```

## Where to read

| File | Owns |
|---|---|
| `app/pipeline.py` | The five stages in order. Start here. Also runnable: `python -m app.pipeline "sentence"` |
| `app/extract.py` + `prompts/extract_profile.md` | Sentence to `AdvertiserProfile`, tag normalisation |
| `app/retrieval.py` | Hard filters, lexical features and weights, semantic scores, RRF, persona selection |
| `app/embeddings.py` | Catalog embeddings, on-disk cache, persona-to-publisher placements |
| `app/rerank.py` + `prompts/rerank_publishers.md` | LLM fit scores and rationales over the shortlist |
| `app/creatives.py` + `prompts/generate_creatives.md` | One creative per persona, claim and disinterest QA, rewrite pass |
| `app/campaign.py` | Budget water-fill, CPM estimates, bid strategy, feasibility, targeting |
| `app/llm.py` | Eden AI client, strict JSON schema calls, prompt loader, response cache |
| `app/schemas.py` | Every data shape: catalog, profile, scores |
| `app/main.py` + `static/` | FastAPI endpoint and the single-page UI |
| `eval/` | 16 sample inputs, hand labels, precision@3 and status checks |
| `tests/` | 12 unit tests for everything that needs no LLM |

## The three exits

- **needs_input**: extraction found no usable signal. The UI shows the clarifying questions and nothing else runs.
- **no_fit**: every publisher failed a hard filter, in practice the B2B rule. The UI lists each exclusion reason.
- **ok**: a full plan. Weak fits are labelled, invented claims are flagged, and the bid box says whether the economics close.
