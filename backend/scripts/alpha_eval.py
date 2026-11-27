"""Live evaluation of the ALPHA framework on real tickers. Spends model budget: run it on purpose, not in CI.

    python scripts/dev.py alpha-eval                      # GOOGL, MSFT, AAPL, two runs each
    python scripts/dev.py alpha-eval --tickers NVDA --runs 3

For each ticker and run it records, and then checks:
  - complete      every pillar was written (none missing)
  - checked       no figure left unconfirmed by the fact check (after the one revision)
  - revenue       the latest annual revenue quoted in Performance matches the company's reported revenue (yfinance, within 1.5%)
  - stable        the stance is the same on every run of the ticker, and no pillar score moves by more than 1
and what it cost (tokens and estimated USD, any provider). Results go to data/output/alpha_eval_<time>.json.
Exit code 1 when a check fails, so it can gate a prompt or model change."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _latest_revenue(ticker: str):
    try:
        import yfinance as yf
        stmt = yf.Ticker(ticker).income_stmt
        for label in ("Total Revenue", "Operating Revenue"):
            if label in stmt.index:
                vals = stmt.loc[label].dropna()
                if len(vals):
                    return float(vals.iloc[0])
    except Exception:
        return None
    return None


def _revenue_quoted(text: str, revenue: float) -> bool:
    from rag.verify import numbers
    for n in numbers.extract(text):
        if not n["pct"] and abs(n["value"] - revenue) <= 0.015 * revenue:
            return True
    return False


async def _run_once(agent, ticker: str, user_id: str):
    from langchain_core.messages import HumanMessage
    from app.services import pricing
    from app.services.app_settings import store as settings_store
    from app.services import app_settings, llm_providers
    from app.services.llm_providers import service as provider_service
    from app.database.connection import SessionLocal
    from app.utils.company_mapping import get_company_name
    from app.utils.usage_tracker import track_usage

    async with SessionLocal() as db:
        settings = await settings_store.effective(db, user_id, None, None)
        creds = await provider_service.credentials_for(db, user_id)
    config = {"configurable": {"thread_id": f"alpha-eval-{ticker}-{time.time_ns()}", "user_id": user_id}}
    inputs = {"messages": [HumanMessage(content=f"ALPHA analysis for {ticker}")], "vectorstore_searched": False, "web_searched": False,
              "vectorstore_quality": "none", "needs_web_fallback": False, "retry_count": 0, "documents": [], "document_sources": {},
              "citation_info": [], "summary_strategy": "single_source", "company_filter": [ticker], "sub_query_analysis": {},
              "sub_query_results": {}, "alpha_mode": True, "alpha_pillar": None, "ticker": ticker,
              "company_name": get_company_name(ticker), "alpha_dimensions": {}, "alpha_report": "", "chart_url": None, "chart_filename": None}
    started = time.perf_counter()
    with track_usage(f"alpha-eval:{ticker}") as tracker, app_settings.use(settings), llm_providers.use_credentials(creds):
        result = await agent.ainvoke(inputs, config)
    seconds = time.perf_counter() - started
    tokens = sum(e.prompt_tokens + e.completion_tokens for e in tracker.entries)
    costs = [pricing.cost(e.model, e.prompt_tokens, e.completion_tokens) for e in tracker.entries]
    return {"report": result.get("alpha_report") or "", "signal": result.get("alpha_result") or {}, "seconds": round(seconds, 1),
            "tokens": tokens, "cost_usd": round(sum(c for c in costs if c), 4), "unpriced_calls": sum(1 for c in costs if c is None)}


async def main(tickers, runs, user_id):
    from app.core.agents_init import AgentBundle
    from app.database.connection import init_db
    from rag.alpha import framework as fw
    init_db()
    bundle = await AgentBundle().build()
    out, failed = {"tickers": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}, False
    try:
        for ticker in tickers:
            ticker = ticker.upper()
            revenue = _latest_revenue(ticker)
            records = []
            for i in range(runs):
                r = await _run_once(bundle.rag_agent, ticker, user_id)
                sig = r["signal"]
                perf = fw.strip_tags(r["report"].split("## P — ", 1)[-1].split("## H — ", 1)[0]) if r["report"] else ""
                rec = {"run": i + 1, "stance": sig.get("stance"), "mean_score": sig.get("mean_score"), "missing": sig.get("missing", []),
                       "fact_issues": sig.get("fact_issues"), "scores": {k: (v or {}).get("score") for k, v in (sig.get("pillar_detail") or {}).items()},
                       "revised": [k for k, v in (sig.get("pillar_detail") or {}).items() if (v or {}).get("revised")],
                       "revenue_ok": None if revenue is None else _revenue_quoted(perf, revenue),
                       "seconds": r["seconds"], "tokens": r["tokens"], "cost_usd": r["cost_usd"], "unpriced_calls": r["unpriced_calls"]}
                records.append(rec)
                print(f"{ticker} run {i + 1}: {rec['stance']} (mean {rec['mean_score']}), missing={rec['missing'] or '-'}, "
                      f"unconfirmed={rec['fact_issues']}, revenue={'ok' if rec['revenue_ok'] else 'n/a' if rec['revenue_ok'] is None else 'MISMATCH'}, "
                      f"{rec['seconds']}s, {rec['tokens']:,} tokens, ${rec['cost_usd']}")
            stances = {r["stance"] for r in records}
            drift = max((abs((a or 0) - (b or 0)) for k in fw.PILLAR_NAMES for a, b in [(records[0]["scores"].get(k), r["scores"].get(k)) for r in records[1:]]),
                        default=0)
            checks = {"complete": all(not r["missing"] for r in records), "checked": all(not r["fact_issues"] for r in records),
                      "revenue": all(r["revenue_ok"] is not False for r in records), "stable": len(stances) == 1 and drift <= 1}
            failed |= not all(checks.values())
            out["tickers"][ticker] = {"checks": checks, "runs": records}
            print(f"{ticker}: " + ", ".join(f"{k}={'pass' if v else 'FAIL'}" for k, v in checks.items()))
    finally:
        await bundle.close()
    path = ROOT.parent / "data" / "output" / f"alpha_eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    total = sum(r["cost_usd"] for t in out["tickers"].values() for r in t["runs"])
    print(f"\nSaved {path}  ·  total estimated cost ${total:.2f}")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", nargs="+", default=["GOOGL", "MSFT", "AAPL"])
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--user-id", default="1", help="whose settings and provider keys to use")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.tickers, max(1, args.runs), args.user_id)))
