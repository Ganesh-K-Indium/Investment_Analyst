OPTIONS INTELLIGENCE AGENT
==========================


OVERVIEW

When you ask "Analyze the options chain of AAPL," the agent scans live options market activity and answers one question: where is real money positioned, and what does that tell us about where the market thinks this stock is going?

It covers up to 6 expiration dates — the nearest 4 near-term weekly/monthly contracts plus 2 long-dated positions — and produces an institutional-style read of sentiment, key price levels, and smart money signals. Every number is computed by deterministic Python code. The AI's only job is to translate the output into plain English.

Data source: Yahoo Finance live options chain (approximately 15-minute delay, free tier)


---


WHAT EACH OUTPUT FIGURE MEANS AND HOW IT IS CALCULATED


1. TOTAL CALL / PUT NOTIONAL  (e.g. "$65.5M calls vs $16.2M puts")

The total dollar value of ALL call (or put) contracts traded today, summed across all analyzed expirations. This is the single most important directional signal — it tells you where real capital is committed, not just where trades happened.

Formula:
    Per contract:  notional = volume x lastPrice x 100
    Total calls:   sum of notional across every call contract in every analyzed expiration
    Total puts:    sum of notional across every put contract in every analyzed expiration

Each options contract covers 100 shares. A contract priced at $1.00 with 95,378 contracts traded equals $9.54M.

Important: Each strike + expiration pair is a separate instrument. $300 calls expiring May 20 and $300 calls expiring May 22 are never combined — they carry different risk and different intent.

AAPL example — how $65.5M is built up:

    May 20  (0 days to expiry)    Calls $25.1M    Puts $5.6M
    May 22  (2 days to expiry)    Calls $19.6M    Puts $4.4M
    May 26  (6 days to expiry)    Calls  $1.7M    Puts $1.3M
    May 27  (7 days to expiry)    Calls  $0.4M    Puts $0.3M
    Aug 21  (93 days to expiry)   Calls $11.8M    Puts $1.9M
    Sep 18  (121 days to expiry)  Calls  $7.0M    Puts $2.7M
    TOTAL                         Calls $65.5M    Puts $16.2M

The long-dated expirations (August and September) contribute $18.8M to call notional. This is institutional or smart money flow. The $65.5M vs $16.2M ratio means for every $1 placed on puts, $4 is placed on calls — a 4-to-1 capital commitment to the upside.


---


2. PUT/CALL RATIO  (e.g. "P/C ratio of 0.39")

Ratio of total put volume to total call volume across all analyzed expirations.

Formula:
    P/C Ratio = Total Put Volume / Total Call Volume

Interpretation:
    Below 0.5    Strongly bullish — far more calls than puts
    0.5 to 0.7   Bullish
    0.7 to 1.0   Neutral
    Above 1.0    Bearish — more put activity than calls

AAPL example: 367,000 call contracts vs 145,000 put contracts gives P/C = 0.39 (strongly bullish)


---


3. BIGGEST TRADES  (e.g. "$300 calls May 20 — $9.54M at $1.00/contract")

The individual strike + expiration positions with the highest dollar flow, ranked by notional.

Formula:
    Notional = volume x lastPrice x 100

Ranked by dollar flow, not contract count. A $50 contract with 1,000 volume ($5M) outranks a $0.05 contract with 100,000 volume ($500K).

What does "$1.00/contract" mean?
The price shown (e.g. $1.00/contract) is the last traded price per share for that option. Since every options contract covers exactly 100 shares, the actual cost to enter one contract is the price multiplied by 100.

Example: "$1.00/contract" means it costs $100 to buy one contract (1.00 x 100). With 95,378 contracts traded, total notional = 95,378 x $1.00 x 100 = $9.54M.

Similarly, "$2.42/contract" on the $297.5 calls means each contract costs $242 to buy (2.42 x 100). Options are always quoted per share, but you must trade in 100-share lots — so a "$1 option" is never just $1 to enter, it is always $100 minimum per contract.

AAPL top trades:
    Rank 1   $300 call May 20     95,378 contracts   quoted at $1.00/share   ($100 per contract)   $9.54M
    Rank 2   $297.5 call May 20   39,264 contracts   quoted at $2.42/share   ($242 per contract)   $9.50M
    Rank 3   $300 call May 22     30,567 contracts   quoted at $2.31/share   ($231 per contract)   $7.06M

Important: options are always quoted per share. Since every contract covers 100 shares, the actual
cost to enter one contract is always the quoted price multiplied by 100. A "$1.00 option" costs
$100 to buy. A "$2.42 option" costs $242 to buy. The agent output shows both — the quoted price
per share and the real cost per contract in brackets.


---


4. KEY LEVELS — SUPPORT AND RESISTANCE

Strikes with the heaviest activity across all expirations become real price levels because of how market makers hedge their books.

    Resistance = top 3 call strikes above current price (summed across all expirations)
    Support    = top 3 put strikes below current price (summed across all expirations)

Why cross-expiration summing is intentional here: If $300 is a heavy call strike in May 20, May 22, and May 26, that makes it a stronger resistance level. It means more market makers are short those calls and will sell stock as price approaches $300 across multiple time frames.

The mechanics: Market makers who sold calls at $300 must sell stock as price rises toward $300 to stay delta-neutral — this creates a mechanical ceiling. Put sellers buy stock near heavy put strikes to hedge — this creates a floor. These are not just chart lines; they are enforced by hedging flows.


---


5. IV SKEW  (e.g. "IV skew of +11.9%")

Whether the market is paying more for downside protection (puts) or upside exposure (calls).

Formula:
    IV Skew = Average Put IV minus Average Call IV

Filters applied before calculating:
    Only contracts where implied volatility is above 1% (removes noise and zero values)
    Only expirations with more than 1 day to expiry — expiry-day IV degrades to noise as contracts approach settlement and is excluded

Interpretation:
    Positive (+11.9%)   Puts are about 12 percentage points more expensive. Market is buying downside insurance despite bullish call flow.
    Negative            Calls more expensive — pure upside demand
    Near zero           Balanced sentiment


---


6. ATM CONCENTRATION  (e.g. "66.6%")

What percentage of today's total volume is clustered within 2% of the current stock price.

Formula:
    ATM window = current price plus or minus 2%
    ATM Concentration % = (volume within ATM window / total volume) x 100

Reported as the average of call ATM% and put ATM%.

Interpretation:
    Above 40%   Traders clustered at-the-money — stock expected to stay near current level (pinning behavior)
    Below 20%   Volume spread into out-of-the-money strikes — directional bet, market expects a move

AAPL: 66.6% within 2% of $299 means strong pinning, typical on expiration week.


---


7. NOTABLE FLOW / UNUSUAL ACTIVITY

Contracts where today's volume is abnormally large, sorted by dollar flow (largest first).

Filter criteria:
    Volume greater than 3 times open interest, AND volume greater than 500 contracts
    Since Yahoo Finance open interest is always 0, the volume above 500 threshold is the effective filter.
    Each expiration is evaluated independently.


---


8. SMART MONEY — LONG-DATED POSITIONING

Unusually high volume in options expiring more than 90 days out. Long-dated options are not day-trades — they require paying more premium per contract and tying up capital for months. Elevated volume at a specific strike in a long-dated expiration signals that a large participant has a directional view on where the stock will be in 3 to 6 months.

Note: Open Interest is not used here because Yahoo Finance does not provide intraday OI data. Volume is the signal instead.

All three filters must pass:
    Expiration is more than 90 days away
    Volume at a single strike is above 50 contracts AND in the top 15% of strikes for that expiration
    Strike is within 40% of current price (removes far out-of-the-money noise)

Labels:
    ACCUMULATING   Call volume dominates — bullish multi-month conviction
    HEDGING        Put volume dominates — institutional downside protection
    MIXED          Balanced call and put volume

AAPL example output:
    Assessment: ACCUMULATING  (dominant strike $300)
    $305 call Aug 21  1,092 contracts  $1.45M notional  (93 days out)
    $330 call Aug 21    966 contracts  $0.49M notional
    $300 call Aug 21    454 contracts  $0.70M notional
    This means institutional players are buying upside exposure at $300-$350 for August — a 3-month bullish bet.


---


THE ACTIVITY CHART

Every analysis automatically includes a two-panel chart generated inside the analytics engine and returned as a URL. No separate step needed.

Expiration selected for chart: The nearest expiration with at least 1 day remaining. Expiration-day contracts are always skipped — on expiry day activity collapses to 2 or 3 near-ATM strikes and the chart becomes a meaningless single spike.

    0 days (expiry day)   High volume but useless for the chart — single spike
    2 days (next weekly)  Best choice — high volume, clean spread across strikes
    6 days (+1 week)      Thinner but still readable

Top panel — Volume Distribution: Calls (green) vs puts (red) by strike. Gold vertical line marks current price. Top 3 call and put strikes are labeled.

Bottom panel — Notional Dollar Flow in millions: Same strikes scaled by dollar value. Shows where real capital sits. A $50 contract with 1,000 volume towers over a $0.05 contract with 50,000 volume.

Range shown: within 10% of current price, zero-activity strikes removed.


---


DATA LIMITATIONS (Yahoo Finance Free Tier)

Intraday Open Interest      Not available — always returns 0. Volume used as proxy.
Max pain                    Not computed — requires accurate open interest.
Greeks (Delta, Gamma)       Not provided by Yahoo Finance.
IV reliability              Reliable only during market hours for near-ATM strikes. Excluded on expiry day.
Dark pool / block trades    Not available. Requires paid data feed.
Historical flow comparison  No caching — each query is a live snapshot.
Data delay                  Approximately 15 minutes.
