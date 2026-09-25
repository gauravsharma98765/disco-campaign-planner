"""
Score eval/results.json against eval/labels.json.

    .venv/bin/python -m eval.metrics

Precision@3: of the top-3 recommended publishers, how many a planner would also have picked.
Also reports fusion-only precision (the deterministic order before the LLM reranker), so you
can see what the reranker adds. Status checks cover the B2B and no-signal cases.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def top3(publishers: list[dict], key: str) -> list[str]:
    ranked = [s for s in publishers if s["status"] != "excluded"]
    ranked.sort(key=lambda s: (-(s.get(key) or 0)) if key == "fit_score" else (s["fused_rank"] or 999))
    return [s["name"] for s in ranked[:3]]


def main() -> None:
    results = json.loads((HERE / "results.json").read_text())
    labels = {k: v for k, v in json.loads((HERE / "labels.json").read_text()).items() if not k.startswith("_")}
    rows, status_ok, status_total = [], 0, 0
    for n, label in sorted(labels.items(), key=lambda kv: int(kv[0])):
        r = results.get(n)
        if not r:
            print(f"#{n:>2}: no result (run eval.run_examples first)")
            continue
        if "status" in label:
            status_total += 1
            hit = r["status"] == label["status"]
            status_ok += hit
            print(f"#{n:>2}: status {r['status']:<12} expected {label['status']:<12} {'OK' if hit else 'MISS'}")
            continue
        if r["status"] != "ok":
            print(f"#{n:>2}: status {r['status']} but publishers were expected")
            rows.append((n, 0.0, 0.0))
            continue
        want = set(label["publishers"])
        rerank_top = top3(r["publishers"], "fit_score")
        fused_top = top3(r["publishers"], "fused_rank")
        p_rerank = len(set(rerank_top) & want) / 3
        p_fused = len(set(fused_top) & want) / 3
        rows.append((n, p_rerank, p_fused))
        print(f"#{n:>2}: P@3 reranked {p_rerank:.2f}  fusion-only {p_fused:.2f}   got {rerank_top}  want {sorted(want)}")
    if rows:
        print(f"\nmean P@3  reranked: {sum(r[1] for r in rows) / len(rows):.2f}   fusion-only: {sum(r[2] for r in rows) / len(rows):.2f}   (n={len(rows)})")
    if status_total:
        print(f"status checks: {status_ok}/{status_total}")


if __name__ == "__main__":
    main()
