"""
Form4 Transaction Verifier
Usage: python check_form4.py <TICKER>
Prints a full breakdown of all insider transactions for the ticker so you can
verify it against the AI-generated report.
"""

import sqlite3
import sys
from collections import defaultdict

DB_PATH = "portfolios.db"

# SEC transaction code descriptions
TRANSACTION_CODE_MAP = {
    "P": "Open-market Purchase",
    "S": "Open-market Sale",
    "A": "Grant / Award",
    "D": "Disposition to company",
    "F": "Tax withholding (shares withheld)",
    "G": "Gift",
    "M": "Option exercise",
    "C": "Conversion of derivative",
    "J": "Other acquisition/disposition",
    "I": "Discretionary transaction",
    "U": "Tender of shares",
    "X": "Option exercise (expired)",
    "Z": "Voting trust deposit/withdrawal",
}


def get_transactions(ticker: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            rpt_owner_name,
            rpt_owner_title,
            is_director,
            is_officer,
            is_ten_percent_owner,
            transaction_date,
            transaction_code,
            transaction_acquired_disposed_code AS ad_code,
            transaction_shares,
            transaction_price_per_share       AS price,
            transaction_value,
            security_title
        FROM form4_transactions
        WHERE UPPER(issuer_symbol) = UPPER(?)
        GROUP BY
            rpt_owner_name,
            transaction_date,
            transaction_code,
            transaction_acquired_disposed_code,
            transaction_shares,
            transaction_price_per_share
        ORDER BY transaction_date DESC, rpt_owner_name
        """,
        (ticker,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def summarise(ticker: str):
    rows = get_transactions(ticker)

    if not rows:
        print(f"  No Form4 transactions found for ticker: {ticker.upper()}")
        return

    print("=" * 70)
    print(f"  FORM 4 TRANSACTION SUMMARY — {ticker.upper()}")
    print("=" * 70)
    print(f"  Total raw transactions: {len(rows)}\n")

    # ── Aggregates ────────────────────────────────────────────────────────
    total_bought_value  = 0.0
    total_sold_value    = 0.0
    total_bought_shares = 0.0
    total_sold_shares   = 0.0

    by_person   = defaultdict(lambda: {"bought": 0.0, "sold": 0.0,
                                        "bought_shares": 0.0, "sold_shares": 0.0,
                                        "title": "", "txn_count": 0})
    by_code     = defaultdict(lambda: {"count": 0, "value": 0.0, "shares": 0.0})

    for r in rows:
        ad   = (r["ad_code"] or "").upper()
        val  = r["transaction_value"]  or 0.0
        shrs = r["transaction_shares"] or 0.0
        code = (r["transaction_code"] or "?").upper()
        name = r["rpt_owner_name"] or "Unknown"
        title = r["rpt_owner_title"] or ""

        by_person[name]["title"]       = title
        by_person[name]["txn_count"]  += 1

        if ad == "A":
            total_bought_value  += val
            total_bought_shares += shrs
            by_person[name]["bought"]        += val
            by_person[name]["bought_shares"] += shrs
        elif ad == "D":
            total_sold_value  += val
            total_sold_shares += shrs
            by_person[name]["sold"]        += val
            by_person[name]["sold_shares"] += shrs

        by_code[code]["count"]  += 1
        by_code[code]["value"]  += val
        by_code[code]["shares"] += shrs

    net_flow = total_bought_value - total_sold_value

    # ── Overall totals ────────────────────────────────────────────────────
    print("  OVERALL TOTALS (Acquired vs Disposed)")
    print("-" * 70)
    print(f"  Total Bought (Acquired) : ${total_bought_value:>15,.2f}   ({total_bought_shares:>12,.0f} shares)")
    print(f"  Total Sold  (Disposed)  : ${total_sold_value:>15,.2f}   ({total_sold_shares:>12,.0f} shares)")
    print(f"  Net Insider Flow        : ${net_flow:>15,.2f}")
    print(f"  Recommendation hint     : {'BUY signal' if net_flow > 0 else 'SELL signal' if net_flow < 0 else 'Neutral'}")
    print()

    # ── By transaction code ───────────────────────────────────────────────
    print("  BREAKDOWN BY TRANSACTION CODE")
    print("-" * 70)
    print(f"  {'Code':<6} {'Description':<30} {'Count':>6} {'Total Shares':>14} {'Total Value':>16}")
    print(f"  {'-'*6} {'-'*30} {'-'*6} {'-'*14} {'-'*16}")
    for code, d in sorted(by_code.items()):
        desc = TRANSACTION_CODE_MAP.get(code, "Unknown")
        print(f"  {code:<6} {desc:<30} {d['count']:>6} {d['shares']:>14,.0f} ${d['value']:>15,.2f}")
    print()

    # ── By insider ────────────────────────────────────────────────────────
    print("  BREAKDOWN BY INSIDER")
    print("-" * 70)
    for name, d in sorted(by_person.items()):
        print(f"\n  {name}  [{d['title'] or 'N/A'}]  — {d['txn_count']} transaction(s)")
        print(f"    Bought : ${d['bought']:>12,.2f}  ({d['bought_shares']:>10,.0f} shares)")
        print(f"    Sold   : ${d['sold']:>12,.2f}  ({d['sold_shares']:>10,.0f} shares)")
        net = d["bought"] - d["sold"]
        print(f"    Net    : ${net:>12,.2f}")

    # ── Raw transaction log ───────────────────────────────────────────────
    print("\n")
    print("  RAW TRANSACTION LOG")
    print("-" * 70)
    print(f"  {'Date':<12} {'Name':<28} {'Code':<4} {'A/D':<4} {'Shares':>12} {'Price':>10} {'Value':>14} Security")
    print(f"  {'-'*12} {'-'*28} {'-'*4} {'-'*4} {'-'*12} {'-'*10} {'-'*14} --------")
    for r in rows:
        date  = str(r["transaction_date"] or "")[:10]
        name  = (r["rpt_owner_name"] or "")[:27]
        code  = r["transaction_code"] or "?"
        ad    = r["ad_code"] or "?"
        shrs  = r["transaction_shares"] or 0
        price = r["price"] or 0
        val   = r["transaction_value"] or 0
        sec   = (r["security_title"] or "")[:20]
        print(f"  {date:<12} {name:<28} {code:<4} {ad:<4} {shrs:>12,.0f} {price:>10.2f} ${val:>13,.2f} {sec}")

    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_form4.py <TICKER>")
        print("Example: python check_form4.py COP")
        sys.exit(1)

    summarise(sys.argv[1])
