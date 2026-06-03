"""
verify_output.py — Paste the agent output and verify every number against live Yahoo Finance.

Usage:
    cd quant/options_mcp

    # Paste agent output (press Ctrl+D when done):
    python3 verify_output.py TSLA

    # Or pipe it:
    echo "...agent output..." | python3 verify_output.py TSLA

    # Or heredoc:
    python3 verify_output.py TSLA << 'EOF'
    [paste agent output here]
    EOF
"""

import sys
import re
import yfinance as yf
import pandas as pd
from datetime import datetime, date, timezone

# ── Read inputs ────────────────────────────────────────────────────────────────
TICKER = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"

print(f"\nPaste the agent output below, then press Ctrl+D (Mac/Linux) or Ctrl+Z Enter (Windows):\n")
try:
    agent_text = sys.stdin.read().strip()
except KeyboardInterrupt:
    sys.exit(0)

if not agent_text:
    print("No input provided. Exiting.")
    sys.exit(0)

# ── Parse agent output ─────────────────────────────────────────────────────────

def extract_strike_volumes(text):
    """Find all '$X strike ... Y,YYY contracts' or '$X ... Y,YYY contracts' patterns."""
    claims = []
    # Pattern: $420 strike ... 78,164 contracts  (within same sentence)
    pattern = r'\$(\d+(?:\.\d+)?)\s+(?:strike\s+)?(?:call|put)?[^.]*?(\d{1,3}(?:,\d{3})+)\s+contracts'
    for m in re.finditer(pattern, text, re.IGNORECASE):
        strike = float(m.group(1))
        volume = int(m.group(2).replace(",", ""))
        # Determine call or put from context (look backwards ~100 chars)
        start = max(0, m.start() - 100)
        context = text[start:m.end()].lower()
        opt_type = "put" if "put" in context else "call"
        claims.append({"strike": strike, "volume": volume, "type": opt_type, "raw": m.group(0)})
    return claims

def extract_pc_ratio(text):
    """Find P/C ratio claim."""
    # "put/call ratio of 0.54" or "put/call ratio below 0.7" or "above 1.0"
    m = re.search(r'put[/\-]?call ratio[^\d]*(\d+\.\d+)', text, re.IGNORECASE)
    if m: return float(m.group(1))
    # "ratio slightly above 1.0" / "below 0.7"
    m = re.search(r'ratio\s+(?:slightly\s+)?(?:above|below|of|around|near)\s+(\d+\.\d+)', text, re.IGNORECASE)
    if m: return float(m.group(1))
    return None

def extract_sentiment(text):
    """Find stated sentiment."""
    m = re.search(r'(?:overall\s+)?sentiment\s+is\s+(bullish|bearish|neutral|strongly bullish|mildly bullish)', text, re.IGNORECASE)
    if m: return m.group(1).lower()
    if re.search(r'strongly bullish', text, re.IGNORECASE): return "strongly bullish"
    if re.search(r'mildly bullish',   text, re.IGNORECASE): return "mildly bullish"
    if re.search(r'\bbullish\b',      text, re.IGNORECASE): return "bullish"
    if re.search(r'\bbearish\b',      text, re.IGNORECASE): return "bearish"
    if re.search(r'\bneutral\b',      text, re.IGNORECASE): return "neutral"
    return None

def extract_support_resistance(text):
    """Find stated support and resistance levels."""
    support    = []
    resistance = []
    for m in re.finditer(r'support[^.]*?\$(\d+(?:\.\d+)?)', text, re.IGNORECASE):
        support.append(float(m.group(1)))
    for m in re.finditer(r'resistance[^.]*?\$(\d+(?:\.\d+)?)', text, re.IGNORECASE):
        resistance.append(float(m.group(1)))
    return support, resistance

def extract_atm_interpretation(text):
    """Check if agent says consolidation or breakout."""
    if re.search(r'consolidat|pinning|stuck|range.bound|small move', text, re.IGNORECASE):
        return "consolidation"
    if re.search(r'breakout|big move|directional', text, re.IGNORECASE):
        return "breakout"
    return None

# ── Fetch live data ────────────────────────────────────────────────────────────
print(f"Fetching live data for {TICKER}...")
tk            = yf.Ticker(TICKER)
current_price = tk.fast_info.last_price
expirations   = list(tk.options)
today         = date.today()

def dte(exp): return (datetime.strptime(exp, "%Y-%m-%d").date() - today).days

expiry_day = [e for e in expirations if dte(e) == 0]
near_term  = [e for e in expirations if 0 < dte(e) <= 90][:4]
long_dated = [e for e in expirations if dte(e) > 90][:2]
to_analyze = expiry_day + near_term + long_dated

all_calls, all_puts = [], []
for exp in to_analyze:
    try:
        chain = tk.option_chain(exp)
        for df, label in [(chain.calls, "call"), (chain.puts, "put")]:
            df = df.copy()
            df["volume"]   = pd.to_numeric(df["volume"],   errors="coerce").fillna(0)
            df["lastPrice"]= pd.to_numeric(df["lastPrice"],errors="coerce").fillna(0)
            df["notional"] = df["volume"] * df["lastPrice"] * 100
            df["expiration"] = exp
            df["dte"]        = dte(exp)
            if label == "call": all_calls.append(df)
            else:               all_puts.append(df)
    except Exception: pass

calls_df = pd.concat(all_calls, ignore_index=True) if all_calls else pd.DataFrame()
puts_df  = pd.concat(all_puts,  ignore_index=True) if all_puts  else pd.DataFrame()

total_call_vol = calls_df["volume"].sum()
total_put_vol  = puts_df["volume"].sum()
agg_pc         = round(total_put_vol / total_call_vol, 4) if total_call_vol > 0 else None

atm_lo, atm_hi = current_price * 0.98, current_price * 1.02
call_atm = calls_df[(calls_df["strike"] >= atm_lo) & (calls_df["strike"] <= atm_hi)]["volume"].sum()
put_atm  = puts_df[ (puts_df["strike"]  >= atm_lo) & (puts_df["strike"]  <= atm_hi)]["volume"].sum()
atm_conc = round(((call_atm / total_call_vol) + (put_atm / total_put_vol)) / 2 * 100, 1)

above      = calls_df[calls_df["strike"] > current_price]
below      = puts_df[ puts_df["strike"]  < current_price]
resistance = sorted(above.groupby("strike")["volume"].sum().nlargest(3).index.tolist())
support    = sorted(below.groupby("strike")["volume"].sum().nlargest(3).index.tolist(), reverse=True)

actual_sentiment = "strongly bullish" if agg_pc and agg_pc < 0.5 else \
                   "bullish"          if agg_pc and agg_pc < 0.7 else \
                   "neutral"          if agg_pc and agg_pc < 1.0 else "bearish"
actual_atm_label = "consolidation" if atm_conc > 40 else ("breakout" if atm_conc < 20 else "mixed")

def get_volume(df, strike, exp=None):
    mask = df["strike"] == strike
    if exp: mask = mask & (df["expiration"] == exp)
    rows = df[mask]
    if rows.empty: return 0
    return int(rows.loc[rows["volume"].idxmax(), "volume"])

def get_expiry(df, strike):
    sub = df[df["strike"] == strike]
    if sub.empty: return None
    return sub.loc[sub["volume"].idxmax(), "expiration"]

# ── Parse agent claims ─────────────────────────────────────────────────────────
claimed_strikes    = extract_strike_volumes(agent_text)
claimed_pc         = extract_pc_ratio(agent_text)
claimed_sentiment  = extract_sentiment(agent_text)
claimed_support, claimed_resistance = extract_support_resistance(agent_text)
claimed_atm        = extract_atm_interpretation(agent_text)

# ── Print results ──────────────────────────────────────────────────────────────
passed = failed = 0

def row(label, claimed, actual, ok):
    global passed, failed
    if ok: passed += 1
    else:  failed += 1
    icon = "✅" if ok else "❌"
    print(f"  {icon}  {label:<40}  claimed: {str(claimed):<12}  actual: {actual}")

print(f"\n{'═'*70}")
print(f"  VERIFICATION — {TICKER}   ${current_price:.2f}   {today}")
print(f"{'═'*70}")

# Strike volumes
print(f"\n  STRIKE VOLUMES")
print(f"  {'─'*65}")
exp0 = expiry_day[0] if expiry_day else (near_term[0] if near_term else None)
for c in claimed_strikes:
    strike = c["strike"]
    opt_df = calls_df if c["type"] == "call" else puts_df
    best_exp = get_expiry(opt_df, strike)
    actual_vol = get_volume(opt_df, strike, best_exp)
    diff_pct = abs(actual_vol - c["volume"]) / max(actual_vol, 1) * 100
    ok = diff_pct <= 5
    exp_label = f"[{best_exp} DTE={dte(best_exp)}]" if best_exp else ""
    label = f"${strike} {c['type']}"
    row(label, f"{c['volume']:,}", f"{actual_vol:,}  {exp_label}", ok)

# P/C ratio
print(f"\n  PUT/CALL RATIO & SENTIMENT")
print(f"  {'─'*65}")
if claimed_pc is not None:
    diff = abs(agg_pc - claimed_pc) / max(agg_pc, 0.01) * 100
    ok   = diff <= 15
    row("P/C ratio", claimed_pc, agg_pc, ok)

if claimed_sentiment:
    ok = claimed_sentiment.replace("mildly ", "").replace("strongly ", "") == actual_sentiment.replace("strongly ", "")
    row("Sentiment", claimed_sentiment, actual_sentiment, ok)

# ATM concentration
print(f"\n  ATM CONCENTRATION")
print(f"  {'─'*65}")
if claimed_atm:
    ok = claimed_atm == actual_atm_label
    row("Consolidation vs breakout", claimed_atm, f"{actual_atm_label} ({atm_conc}%)", ok)

# Support / Resistance
print(f"\n  SUPPORT & RESISTANCE")
print(f"  {'─'*65}")
for s in set(claimed_support):
    ok = s in support
    row(f"Support at ${s}", "mentioned", f"{'✓ confirmed' if ok else '✗ not in top-3'} {support}", ok)
for r in set(claimed_resistance):
    ok = r in resistance
    row(f"Resistance at ${r}", "mentioned", f"{'✓ confirmed' if ok else '✗ not in top-3'} {resistance}", ok)

# Summary
print(f"\n{'═'*70}")
print(f"  RESULT:  {passed} PASSED   {failed} FAILED   ({passed+failed} checks total)")
print(f"  Data: Yahoo Finance  |  {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
print(f"{'═'*70}\n")
