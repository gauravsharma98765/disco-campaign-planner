"""
Run the pipeline over data/example_advertisers.txt and print a compact table.
Results are saved to eval/results.json so you can inspect them without re-spending LLM calls.

    .venv/bin/python -m eval.run_examples              # everything, full pipeline
    .venv/bin/python -m eval.run_examples --fast       # skip the LLM reranker and creatives
    .venv/bin/python -m eval.run_examples 1 5 15       # only these example numbers
"""
import json
import sys
from pathlib import Path

from app.catalog import examples
from app.pipeline import run_plan

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results.json"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fast = "--fast" in sys.argv
    by_n = {e["n"]: e["text"] for e in examples()}
    wanted = [int(a) for a in args] or sorted(by_n)
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}

    for n in wanted:
        text = by_n[n]
        print(f"\n=== #{n}: {text[:90]}{'...' if len(text) > 90 else ''}")
        try:
            r = run_plan(text, deterministic_only=fast)
        except Exception as ex:  # noqa: BLE001
            print("   FAILED:", type(ex).__name__, str(ex)[:200])
            continue
        results[str(n)] = {"input": text, **r}
        RESULTS.write_text(json.dumps(results, indent=1))
        report(r)


def report(r: dict) -> None:
    p = r["profile"]
    print(f"   status={r['status']} clarity={p['clarity']} consumer={p['is_consumer_commerce']} "
          f"tier={p['price_tier']} ~${p['estimated_price_usd']:.0f} model={p['business_model']} "
          f"gender={p['target_gender']} age={p['target_age_min']}-{p['target_age_max']}")
    print(f"   cats={p['catalog_categories']} primary={p['primary_subcategory']!r} subs={p['catalog_subcategories']}")
    print(f"   affinities={p['persona_affinities']} values={p['values']}")
    if p["assumptions"]:
        print(f"   assumptions: {p['assumptions'][:2]}")
    if r["status"] != "ok":
        print(f"   -> {r['status']}; questions: {p['clarifying_questions']}")
        if r["status"] == "no_fit":
            print(f"   -> excluded all: {r['publishers'][0]['exclusion_reason']}")
        return
    rec = [s for s in r["publishers"] if s["status"] == "recommended"]
    con = [s for s in r["publishers"] if s["status"] == "considered"]
    exc = [s for s in r["publishers"] if s["status"] == "excluded"]
    print(f"   recommended ({len(rec)}): " + " | ".join(f"{s['name']} fit{s['fit_score']} (lex{s['lexical_rank']} sem{s['semantic_rank']} rrf{s['fused_rank']})" for s in rec))
    print(f"   considered ({len(con)}): " + ", ".join(f"{s['name']} rrf{s['fused_rank']}" for s in con[:6]))
    if exc:
        print(f"   excluded ({len(exc)}): {', '.join(s['name'] for s in exc)} :: {exc[0]['exclusion_reason'][:80]}")
    print("   personas: " + " | ".join(f"{s['fused_rank']}.{s['name']}{'*' if s['selected'] else ''}" for s in r["personas"][:6]))
    for c in r.get("creatives", []):
        print(f"   [{c['persona_id']}] {c['headline']!r} / {c['body'][:80]!r} / {c['cta']}")
    for w in r.get("creative_warnings", []):
        print(f"   WARNING {w['persona_id']}: {w['warning']}")
    cfg = r["config"]
    print("   allocation: " + ", ".join(f"{row['name']} {row['allocation_pct']}%" for row in cfg["publishers"])
          + f" | bid: {cfg['bid_strategy']['pricing_model']}/{cfg['bid_strategy']['strategy']}")


if __name__ == "__main__":
    main()
