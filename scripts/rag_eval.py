#!/usr/bin/env python3
"""
Run the RAG golden set against a running API and score it.

    EVAL_BASE_URL=http://localhost:8000 EVAL_TOKEN=<access token> EVAL_THREAD_ID=<portfolio session thread id> \
        python scripts/rag_eval.py [tests/rag_eval/golden.jsonl] [--out out.json] [--baseline base.json]

Each line of the golden file is one case:
    {"id": "aapl-rev-24", "question": "...", "framework": null,
     "expected": {"facts": ["$391.0 billion"], "must_not_include": [], "sources": ["10-K", "2024"]}}
A case with no "expected" is scored only on verification, latency and cost. Fill in expected facts from the filings to measure
accuracy. With --baseline, exits 1 when recall, retrieval or the verified rate regress (use it in CI).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rag.eval import metrics  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("golden", nargs="?", default="tests/rag_eval/golden.jsonl")
    ap.add_argument("--out"), ap.add_argument("--baseline")
    a = ap.parse_args()
    base_url, token, thread = os.getenv("EVAL_BASE_URL", "http://localhost:8000"), os.getenv("EVAL_TOKEN"), os.getenv("EVAL_THREAD_ID")
    if not token or not thread:
        print("Set EVAL_TOKEN and EVAL_THREAD_ID (a portfolio session that contains the companies in the golden set).", file=sys.stderr)
        return 2
    cases = [json.loads(l) for l in Path(a.golden).read_text().splitlines() if l.strip() and not l.startswith("#")]
    rows = []
    with httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=300) as c:
        for case in cases:
            t = time.time()
            r = c.post("/ask", json={"query": case["question"], "thread_id": thread, "framework": case.get("framework"), "settings_override": case.get("settings")})
            row = metrics.score_case(case, r.json() if r.status_code == 200 else {"answer": ""}, time.time() - t)
            row["http"] = r.status_code
            rows.append(row)
            print(f"{case['id']:<28} http={r.status_code} recall={row['fact_recall']} retrieval={row['retrieval_hit']} verify={row['verification']} {row['latency_s']}s")
    summary = metrics.summarize(rows)
    print(json.dumps(summary, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    if a.baseline:
        bad = metrics.regressions(json.loads(Path(a.baseline).read_text())["summary"], summary)
        for b in bad:
            print("REGRESSION:", b, file=sys.stderr)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
