OPTIONS AGENT OUTPUT VERIFICATION — AAPL 2026-05-27
======================================================

This document verifies each claim in the agent output against live Yahoo Finance data,
documents the bugs found, the fixes applied, and confirms the corrected numbers.

Current price at time of analysis: $308.33
Expiration checked: 2026-05-29 (DTE=2, nearest meaningful expiry)


---


AGENT OUTPUT UNDER REVIEW
--------------------------

Bullish signals:
  "Traders are showing strong interest in call options at the $310 strike for May 29,
   with a notable volume of 21,028 contracts."

  "There is significant call activity at the $350 strike for September 18,
   with 28,941 contracts traded."

Bearish signals:
  "Heavy put activity is observed at the $310 strike for May 29, with 9,418 contracts traded."
  "$315 strike for the same expiration also shows significant put volume."

Summary:
  "Put/call ratio below 0.7."
  "The lack of ATM concentration suggests the market is expecting a breakout."


---


CLAIM-BY-CLAIM VERIFICATION
-----------------------------

CLAIM 1: $310 call May 29 — 21,028 contracts
Status: CORRECT

Live data from Yahoo Finance:
    strike    volume    lastPrice    openInterest
    $310      21,028    $1.79        12,510

Volume of 21,028 confirmed. Each contract costs $179 to buy ($1.79 x 100 shares).
Total notional for this strike: 21,028 x $1.79 x 100 = $3.76M


---


CLAIM 2: $350 call Sep 18 — 28,941 contracts traded
Status: WRONG — this was a BUG

Live data from Yahoo Finance:
    strike    volume    lastPrice    openInterest
    $350      3,110     $4.37        28,941

Actual volume today was only 3,110 contracts, not 28,941.
28,941 is the openInterest — contracts carried over from prior sessions, not today's trades.

Root cause: The analytics engine was switching to openInterest as the activity metric when OI
was available (which it is for long-dated monthly expirations like Sep 18). This caused the LLM
to read the OI value and report it as "contracts traded" — which is incorrect.

Fix applied: Forced all activity metrics to volume-only throughout analytics.py.
Three code locations changed:
    1. get_chain_for_chart():    removed "oi" branch, always uses act_col = "volume"
    2. _fetch_chains():          removed "oi" branch, always uses act_col = "volume"
    3. _add_activity_col():      changed from OI-fallback to df["activity"] = df["volume"]

Corrected number: $350 call Sep 18 — 3,110 contracts traded today


---


CLAIM 3: $310 put May 29 — 9,418 contracts
Status: CORRECT

Live data from Yahoo Finance:
    strike    volume    lastPrice    openInterest
    $310      9,418     $3.40        2,983

Volume of 9,418 confirmed. Each contract costs $340 to buy ($3.40 x 100).
Total notional: 9,418 x $3.40 x 100 = $3.20M


---


CLAIM 4: "$315 strike also shows significant put volume"
Status: PARTIALLY CORRECT — agent missed bigger puts

Live data for May 29 puts, ranked by volume:
    $310     9,418 contracts    ← correctly cited
    $305     7,216 contracts    ← NOT mentioned (2nd biggest)
    $300     5,862 contracts    ← NOT mentioned (3rd biggest)
    $307.5   4,526 contracts    ← NOT mentioned (4th biggest)
    $315     2,730 contracts    ← agent cited this as "significant"

$315 is valid but the agent skipped three puts with more volume ($305, $300, $307.5).
This is an output quality issue with the previous system prompt, not a data bug.


---


CLAIM 5: "Put/call ratio below 0.7"
Status: CORRECT

Computed from May 29 data:
    Total call volume:   101,313 contracts
    Total put volume:     47,810 contracts
    P/C ratio = 47,810 / 101,313 = 0.47

0.47 is below 0.7 — strongly bullish threshold. Confirmed correct.


---


CLAIM 6: "Lack of ATM concentration suggests a breakout"
Status: WRONG — agent stated the opposite of what the data shows

ATM window: ±2% of $308.33 = $302.16 to $314.50

    Call volume within ATM window:   48,220 out of 101,313 total = 47.6%
    Put volume within ATM window:    26,148 out of 47,810 total  = 54.8%
    Average ATM concentration:       51.2%

A concentration of 51.2% means MORE THAN HALF of all activity is clustered within 2% of
current price. The correct interpretation is PINNING / CONSOLIDATION — the market expects the
price to stay near $308 through this expiry. The agent said the opposite (breakout expected).

This was a hallucination caused by the LLM misinterpreting the ATM concentration value.
The system prompt has been updated to reinforce: above 40% = consolidation, not breakout.


---


CORRECTED NUMBERS SUMMARY
--------------------------

After fixes, here is what the analytics engine now produces:

    Current price:           $308.33

    Put/Call ratio:          0.47  (below 0.5 — strongly bullish)
    Sentiment:               BULLISH

    ATM concentration:       56.7%  → market expects price to stay near $308, not break out

    Support levels:          $307.5 / $305.0 / $300.0  (top put-heavy strikes below price)
    Resistance levels:       $310.0 / $312.5 / $315.0  (top call-heavy strikes above price)

Top 5 calls by notional (within ±20% of current price — deep ITM noise filtered):
    $310  May 29   21,028 contracts   $1.79/share ($179/contract)   $3.76M
    $270  May 27      597 contracts   $39.90/share ($3,990/contract) $2.38M
    $272.5 May 27     640 contracts   $36.32/share ($3,632/contract) $2.32M
    $310  May 27   28,868 contracts   $0.76/share ($76/contract)    $2.19M
    $312.5 May 29  21,213 contracts   $1.00/share ($100/contract)   $2.12M

Top 5 puts by notional:
    $310  May 29    9,418 contracts   $3.40/share ($340/contract)   $3.20M
    $310  May 27   10,428 contracts   $2.52/share ($252/contract)   $2.63M
    $315  May 29    2,730 contracts   $7.29/share ($729/contract)   $1.99M
    $317.5 May 29   1,556 contracts   $9.28/share ($928/contract)   $1.44M
    $307.5 May 29   4,526 contracts   $2.18/share ($218/contract)   $0.99M

Smart money — Sep 18 (DTE=114), ACCUMULATING:
    $300 call   444 contracts today   $1.05M notional
    $320 call   762 contracts today   $0.98M notional
    $325 call   745 contracts today   $0.82M notional
    $310 call   305 contracts today   $0.53M notional
    $315 call   324 contracts today   $0.50M notional
    (Previously showed 28,941 due to OI bug — now correctly shows today's volume)


---


BUGS FIXED IN THIS SESSION
---------------------------

Bug 1: OI used as volume for long-dated expirations
    Where:   analytics.py — get_chain_for_chart(), _fetch_chains(), _add_activity_col()
    Effect:  Sep 18 $350 call showed 28,941 "contracts traded" (was OI, not volume)
    Fix:     Removed all OI-fallback logic. act_col = "volume" everywhere, unconditionally.

Bug 2: Deep ITM contracts inflating top notional list
    Where:   analytics.py — _top_notional_strikes()
    Effect:  $140 call Sep 18 appeared as #1 by notional ($9.2M) due to high intrinsic price
             ($170.60/share). This is 54.6% below current price — not a meaningful directional bet.
    Fix:     Added ±20% strike filter before ranking. Only strikes within 20% of current price
             qualify for the top notional list.

Bug 3: ATM concentration misinterpreted as "lack of concentration"
    Where:   System prompt in langgraph_agent.py
    Effect:  56.7% ATM concentration was described as "suggests breakout" — the exact opposite
    Fix:     Reinforced in system prompt: above 40% = consolidation/pinning, below 20% = breakout.


---


DATA SOURCE NOTE
----------------

All numbers above pulled directly from Yahoo Finance via yfinance on 2026-05-27.
Volume reflects contracts traded today only (intraday snapshot, ~15 min delay).
OpenInterest is available for monthly expirations (Sep 18) but is NOT used anywhere
in the analytics engine — volume is the only signal, consistent throughout.

