YFINANCE OPTIONS DATA — WHAT WE ACTUALLY GET
=============================================

This document shows exactly what Yahoo Finance returns when we query AAPL options on 2026-05-25
(current price $308.82). Every table here is real output from yfinance — no calculations added.
The purpose is to show the raw material our analytics engine works with before any processing.


---


STEP 1: AVAILABLE EXPIRATION DATES
-----------------------------------

The first thing we fetch is the list of all available option expiration dates.
yfinance returns a tuple of date strings like this:

    ticker = yf.Ticker("AAPL")
    ticker.options

Output (AAPL, 2026-05-25):

    Expiration      DTE (days to expiry)
    2026-05-26      1
    2026-05-27      2
    2026-05-29      4
    2026-06-01      7
    2026-06-03      9
    2026-06-05      11
    2026-06-12      18
    2026-06-18      24
    2026-06-26      32
    2026-07-02      38
    2026-07-17      53
    2026-08-21      88
    2026-09-18      116
    2026-10-16      144
    2026-11-20      179
    2026-12-18      207
    2027-01-15      235
    2027-02-19      270
    2027-03-19      298
    2027-06-17      388
    2027-09-17      480
    2027-12-17      571
    2028-01-21      606
    2028-03-17      662
    2028-12-15      935

AAPL has 25 expiration dates available, ranging from tomorrow (DTE=1) to December 2028 (DTE=935).
Our analytics engine selects the nearest 4 near-term expirations (DTE <= 90) plus 1-2 long-dated
ones (DTE > 90) for smart money scanning.


---


STEP 2: THE OPTION CHAIN — RAW COLUMNS
---------------------------------------

For each expiration we call:

    chain = ticker.option_chain("2026-05-27")
    chain.calls   # DataFrame of all call contracts
    chain.puts    # DataFrame of all put contracts

Every row is one strike price. Here are all 14 columns yfinance returns:

    contractSymbol      Unique ID for the contract, e.g. AAPL260527C00300000
    lastTradeDate       Timestamp of the last trade
    strike              Strike price in dollars
    lastPrice           Last traded price per share (NOT per contract — see note below)
    bid                 Best bid price
    ask                 Best ask price
    change              Price change from prior close
    percentChange       Percent price change
    volume              Number of contracts traded TODAY
    openInterest        Number of open contracts (Yahoo Finance limitation — see below)
    impliedVolatility   Market-implied annualised volatility (decimal, e.g. 0.25 = 25%)
    inTheMoney          True/False — whether the option has intrinsic value
    contractSize        Always "REGULAR" (100 shares per contract)
    currency            Always "USD"

Important note on lastPrice: this is the price PER SHARE. To find the actual cost of buying
one contract, multiply by 100. A lastPrice of $1.98 means one contract costs $198 (1.98 x 100).


---


STEP 3: RAW CALLS DATA (expiration 2026-05-27, DTE=2)
------------------------------------------------------

AAPL call contracts near the current price of $308.82 — what the raw DataFrame looks like:

    strike    lastPrice    volume    openInterest    impliedVolatility
    300.00     9.50        1138      1373            0.381 (38.1%)
    302.50     6.63         146       173            0.325 (32.5%)
    305.00     4.96        1113      2211            0.287 (28.7%)
    307.50     3.30        2030       471            0.260 (26.0%)
    310.00     1.98        3123      1534            0.223 (22.3%)
    312.50     1.11        4249       170            0.226 (22.6%)
    315.00     0.47        2034       755            0.227 (22.7%)
    317.50     0.25        1331       103            0.224 (22.4%)

Reading row 3 (strike = $310.00):
    - Last traded option price: $1.98 per share → costs $198 to buy 1 contract
    - 3,123 contracts traded today
    - Notional = 3,123 x $1.98 x 100 = $618,354 (shown as $0.62M)
    - openInterest of 1,534 = number of contracts held open from prior sessions
    - impliedVolatility of 0.223 = market pricing in 22.3% annualised move


---


STEP 4: RAW PUTS DATA (expiration 2026-05-27, DTE=2)
-----------------------------------------------------

    strike    lastPrice    volume    openInterest    impliedVolatility
    295.00     0.13         716       546            0.312 (31.2%)
    297.50     0.19        1211       687            0.280 (28.0%)
    300.00     0.38        6246      3296            0.260 (26.0%)
    302.50     0.62         940       124            0.254 (25.4%)
    305.00     0.90        2356        85            0.240 (24.0%)
    307.50     1.70        2784        38            0.228 (22.8%)
    310.00     2.95        4359        40            0.243 (24.3%)
    312.50     4.40          54         1            0.227 (22.7%)
    315.00     6.39          31        11            0.346 (34.6%)
    317.50     8.40          32         0            0.425 (42.5%)

Reading row 7 (strike = $310.00 put):
    - Last traded price: $2.95 per share → costs $295 to buy 1 contract
    - 4,359 contracts traded today
    - Notional = 4,359 x $2.95 x 100 = $1,285,905 (shown as $1.29M)
    - This is the largest put by dollar flow for this expiration


---


STEP 5: TOP TRADES RANKED BY NOTIONAL (our engine's output)
------------------------------------------------------------

Our engine computes notional = volume x lastPrice x 100 for every row, then ranks by dollar flow.
This is what the "Biggest Trades" section of the report is built from.

Top CALLS by notional (2026-05-27):

    Rank    Strike    Last Price    Volume    notional
    1       $300      $9.50         1,138     $1.08M    (1138 x 9.50 x 100)
    2       $307.50   $3.30         2,030     $0.67M
    3       $310      $1.98         3,123     $0.62M
    4       $305      $4.96         1,113     $0.55M
    5       $312.50   $1.11         4,249     $0.47M

Top PUTS by notional (2026-05-27):

    Rank    Strike    Last Price    Volume    notional
    1       $310      $2.95         4,359     $1.29M    (4359 x 2.95 x 100)
    2       $307.50   $1.70         2,784     $0.47M
    3       $300      $0.38         6,246     $0.24M
    4       $305      $0.90         2,356     $0.21M
    5       $302.50   $0.62           940     $0.06M

Note: $300 puts had the most contracts (6,246) but ranked 3rd by notional because each contract
was only $0.38 ($38 each). The $310 puts had fewer contracts (4,359) but cost $2.95 each ($295),
so they carried more total capital — $1.29M vs $0.24M. Dollar flow always wins over contract count.


---


STEP 6: THE OPENINTEREST PROBLEM
---------------------------------

The openInterest column exists in the raw data but Yahoo Finance does not provide intraday updates.
For near-term weekly contracts it is almost always zero or unreliably stale.

Example from 2026-05-27 puts:

    strike    volume    openInterest
    310.00    4,359     40           ← 4,359 contracts traded today vs only 40 open interest
    307.50    2,784     38
    305.00    2,356     85
    300.00    6,246     3,296
    297.50    1,211     687

The 4,359 volume with only 40 open interest on the $310 put is clearly stale OI data.
The $300 puts are different — 3,296 OI probably reflects a prior session's open interest
being carried over from a monthly expiration. It is not reliable for same-day analysis.

For deeper monthly expirations (e.g. Sep 2026), open interest numbers are more stable and
closer to realistic, but still not updated intraday.

Because of this, our entire analytics pipeline is built on volume (today's trades) not open interest.
Volume is what actually happened today. Open interest is what may or may not still be open.


---


STEP 7: IMPLIED VOLATILITY (IV) — RAW VALUES
---------------------------------------------

impliedVolatility is returned as a decimal. We multiply by 100 to express as a percentage.

For the 2026-05-27 expiration (DTE=2):

    Average Call IV:          64.2%
    Average Put IV:           58.4%
    IV Skew (put IV - call):  -5.8%

The negative skew here means calls are priced slightly more expensively than puts — indicating
upside demand. Our engine filters out contracts with IV below 1% (removes noise and zero values)
and skips expiry-day contracts (DTE=0) entirely because IV degrades to noise as options approach
settlement.

Note: IV of 64.2% sounds extreme but is partly because deep in-the-money and deep out-of-the-money
options on very short-dated expirations can show inflated IV. Near-ATM strikes at DTE=2 show IV
closer to 22-26%, which is more representative of the market's actual volatility expectation.


---


STEP 8: LONG-DATED CALLS (expiration 2026-09-18, DTE=116)
----------------------------------------------------------

This is what the raw data looks like for a long-dated expiration — the type our smart money
scanner analyses. These contracts are 116 days out.

Top calls by notional (2026-09-18):

    Strike    Last Price    Volume    openInterest    impliedVolatility    notional
    $335      $8.06         2,772     1,818           0.249 (24.9%)        $2.23M
    $310      $17.60        1,069     11,907          0.268 (26.8%)        $1.88M
    $275      $41.58          452     4,953           0.337 (33.7%)        $1.88M
    $340      $6.50         2,482     2,432           0.248 (24.8%)        $1.61M
    $300      $23.65          565     26,611          0.286 (28.6%)        $1.34M

Here the openInterest numbers are much more meaningful — $300 calls have 26,611 contracts open,
$310 calls have 11,907 open. This is because monthly/quarterly expirations accumulate OI over
weeks as institutional players build positions gradually. This is the data our smart money
scanner uses to identify 3-6 month directional bets.

For smart money detection, we require:
    - DTE > 90 days
    - Volume at a single strike in the top 15% of all strikes for that expiration
    - Minimum 50 contracts traded today
    - Strike within 40% of current price (filters far out-of-the-money noise)


---


STEP 9: WHAT THE NaN VALUES MEAN
----------------------------------

Some rows have NaN (blank) in the volume column. This means no contracts traded at that strike
today. It does not mean the contract does not exist — it simply had no activity.

From the 2026-05-27 calls:

    Total strikes with NaN volume:   5 out of 36
    Total strikes with NaN volume:   1 out of 29  (puts)

Our engine fills NaN volume with 0 before computing notional, so these rows contribute $0
to all dollar flow calculations and are excluded from charts (zero-activity strikes are removed).


---


STEP 10: SUMMARY — WHAT WE HAVE AND WHAT WE DON'T
---------------------------------------------------

What Yahoo Finance gives us (free tier):

    Available                           Reliable?
    Strike prices                       Yes — always present
    Last traded price (lastPrice)       Yes — best proxy for fair value
    Today's volume                      Yes — primary signal for all analytics
    Implied volatility                  Yes for ATM, unreliable for deep ITM/OTM
    openInterest                        Unreliable intraday; better for monthly expirations
    bid / ask                           Present but not used in our analytics
    inTheMoney flag                     Yes — used to classify ITM vs OTM in output
    All expirations list                Yes — full list including multi-year LEAPs

What Yahoo Finance does NOT give us (free tier):

    Not Available
    Intraday openInterest updates       OI never updates during market hours
    Greeks (delta, gamma, theta, vega)  Not in yfinance at all
    Dark pool / block trade data        Requires paid feed (unusualwhales, etc.)
    Trade direction (buy vs sell)       Cannot tell if contracts were bought or sold
    Historical volume for comparison    Each query is a live snapshot, no history
    Max pain (accurate)                 Requires reliable OI — not feasible here

Data delay: approximately 15 minutes on the free tier.

