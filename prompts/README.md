# Prompts

Every prompt the system sends, as Markdown with `{{variable}}` placeholders. `app/llm.py`
loads them at runtime, so editing a file here changes the running system.

| File | Stage | Filled with | Output schema |
|---|---|---|---|
| `system.md` | all calls | nothing | the system message |
| `extract_profile.md` | 1. extract | advertiser text + the catalog's category, subcategory and persona-affinity vocabularies | `AdvertiserProfile` (app/schemas.py) |
| `rerank_publishers.md` | 3. rerank | the profile as JSON + the top-8 fused shortlist with each publisher's card and ranker evidence | `RerankResult` (app/rerank.py) |
| `generate_creatives.md` | 4. create | the profile as JSON + each selected persona's card, why it was selected, and where it will run | `CreativeSet` (app/creatives.py) |

Responses are requested in strict JSON-schema mode and validated with Pydantic; one repair
round is attempted if validation fails. Identical prompts are served from `data/llm_cache.json`.
