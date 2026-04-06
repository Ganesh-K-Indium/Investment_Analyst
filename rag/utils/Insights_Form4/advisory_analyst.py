
import pandas as pd
import os
from openai import OpenAI
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()  # Load OPENAI_API_KEY from .env

def analyze_transactions(all_data):
    """
    Analyzes aggregated data from multiple Form 4s to provide investment recommendations.
    
    Args:
        all_data (list): List of dictionaries returned by extract_form4_data.
        
    Returns:
        dict: A summary report containing recommendations per Issuer.
    """
    
    # Flatten transactions to a DataFrame for easier analysis
    records = []
    for entry in all_data:
        issuer = entry.get("issuer_name", "Unknown Issuer")
        ticker = entry.get("ticker", None)
        person = entry.get("reporting_person_name", "Unknown Person")
        roles = entry.get("relationship", [])
        
        # Determine strict role level (heuristic)
        role_weight = 1
        if "Officer" in roles: role_weight = 2
        if "Director" in roles: role_weight = 1.5
        if "10% Owner" in roles: role_weight = 1.2
        
        for txn in entry.get("transactions", []):
            try:
                amount = float(txn["amount"].replace(',', ''))
                raw_price = txn["price"].replace('$', '').replace(',', '')
                price = float(raw_price) if raw_price.replace('.', '').lstrip('-').isdigit() else 0.0
                code = txn["code"]
                ad = txn["acquired_disposed"]
                
                # Filter for relevant transaction codes
                # P - Open market or private purchase
                # S - Open market or private sale
                # A - Grant, award or other acquisition (often compensation, less signal than P)
                # D - Disposition to issuer (often tax withholding, less signal than S)
                
                # We focus on P and S for strong signals.
                # A is positive but often routine.
                
                actual_value = amount * price
                bought_value = 0
                sold_value = 0
                
                signal_score = 0
                if code.startswith('P'):
                    signal_score = actual_value # Dollar value bought
                    bought_value = actual_value
                elif code.startswith('S'):
                    signal_score = -actual_value # Dollar value sold
                    sold_value = actual_value
                
                # Adjust for A/D if code is generic (like J or others, though usually P/S dominate)
                # If pure Acquisition vs Disposition
                elif ad == 'A':
                    signal_score = 0.1 * actual_value # Weak positive
                    bought_value = actual_value
                elif ad == 'D':
                    signal_score = -0.1 * actual_value # Weak negative
                    sold_value = actual_value
                    
                records.append({
                    "Issuer": issuer,
                    "Ticker": ticker,
                    "Person": person,
                    "Role": ", ".join(roles),
                    "RoleWeight": role_weight,
                    "Date": txn["date"],
                    "Code": code,
                    "Amount": amount,
                    "Price": price,
                    "Type": ad,
                    "ActualBought": bought_value,
                    "ActualSold": sold_value,
                    "SignalValue": signal_score
                })
            except (ValueError, TypeError):
                continue
                
    if not records:
        return {"error": "No valid transactions found to analyze."}
        
    df = pd.DataFrame(records)
    
    # --- Generate Recommendations ---
    report = {}
    
    api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key) if api_key else None
    
    # Group by Issuer
    for issuer, group in df.groupby("Issuer"):
        if issuer == "Unknown Issuer": continue
        
        # Calculate summary stats for the prompt
        total_signal = group["SignalValue"].sum()
        buy_volume = group["ActualBought"].sum()
        sell_volume = group["ActualSold"].sum()
        txn_count = len(group)
        
        # New calculation parameters for Acquired and Disposed shares
        buy_txns = group[group["Type"] == 'A']
        sell_txns = group[group["Type"] == 'D']
        
        total_acquired = buy_txns["Amount"].sum() if not buy_txns.empty else 0
        total_disposed = sell_txns["Amount"].sum() if not sell_txns.empty else 0
        
        actual_net_flow = total_acquired - total_disposed
        net_cash_flow = buy_volume - sell_volume
        
        acquired_txn_count = len(buy_txns)
        disposed_txn_count = len(sell_txns)
        
        priced_buys = buy_txns[buy_txns["Price"] > 0]
        priced_sells = sell_txns[sell_txns["Price"] > 0]
        
        total_priced_acquired = priced_buys["Amount"].sum() if not priced_buys.empty else 0
        total_priced_disposed = priced_sells["Amount"].sum() if not priced_sells.empty else 0
        
        avg_acquired_price = buy_volume / total_priced_acquired if total_priced_acquired > 0 else 0
        avg_disposed_price = sell_volume / total_priced_disposed if total_priced_disposed > 0 else 0
        
        # Fetch Market Data
        current_price = "N/A"
        ticker = group["Ticker"].iloc[0]
        market_context = ""
        if ticker:
            try:
                stock = yf.Ticker(ticker.strip())
                hist = stock.history(period="1d")
                if not hist.empty:
                    current_price = hist["Close"].iloc[-1]
                    market_context = f"- Current Market Price: ${current_price:,.2f}"
            except Exception as e:
                market_context = f"- Market Data Error: {e}"

        sorted_group = group.sort_values("Date")

        # ------------------------------------------------------------------
        # PRE-COMPUTE per-insider breakdown by transaction code
        # (S = open-market sale, F = tax withholding, P = open-market buy)
        # Injecting pre-computed values prevents LLM hallucination.
        # ------------------------------------------------------------------
        from collections import defaultdict
        insider_stats = defaultdict(lambda: {
            "role": "", "S_val": 0.0, "S_shares": 0,
            "F_val": 0.0, "F_shares": 0,
            "P_val": 0.0, "P_shares": 0,
        })
        for _, row in sorted_group.iterrows():
            p = row["Person"]
            insider_stats[p]["role"] = row["Role"]
            code = str(row["Code"]).upper()
            amt  = int(row["Amount"])
            val  = row["ActualBought"] + row["ActualSold"]
            if code == "S":
                insider_stats[p]["S_val"]    += row["ActualSold"]
                insider_stats[p]["S_shares"] += amt
            elif code == "F":
                insider_stats[p]["F_val"]    += row["ActualSold"]
                insider_stats[p]["F_shares"] += amt
            elif code == "P":
                insider_stats[p]["P_val"]    += row["ActualBought"]
                insider_stats[p]["P_shares"] += amt

        # Build pre-computed table string (only include insiders with any priced activity)
        insider_table_lines = [
            "| Name | Role | Open-Market Sold S ($) | Tax Withheld F ($) | Open-Market Bought P ($) | Net Economic Flow ($) |",
            "|------|------|------------------------|-------------------|--------------------------|----------------------|",
        ]
        for person, d in sorted(insider_stats.items()):
            net = d["P_val"] - d["S_val"] - d["F_val"]
            if d["S_val"] == 0 and d["F_val"] == 0 and d["P_val"] == 0:
                continue  # skip insiders with only zero-dollar transactions
            insider_table_lines.append(
                f"| {person} | {d['role'] or 'N/A'} "
                f"| ${d['S_val']:>14,.2f} ({d['S_shares']:,} sh) "
                f"| ${d['F_val']:>14,.2f} ({d['F_shares']:,} sh) "
                f"| ${d['P_val']:>14,.2f} ({d['P_shares']:,} sh) "
                f"| ${net:>14,.2f} |"
            )
        insider_table_str = "\n".join(insider_table_lines)

        # Build transaction log with behavioral context
        txn_list_str = ""
        for _, row in sorted_group.iterrows():
            behavior_note = ""
            if isinstance(current_price, (int, float)) and row["Price"] > 0:
                diff_pct = ((current_price - row["Price"]) / row["Price"]) * 100
                if row["Code"] in ["S", "F"]:
                    behavior_note = (
                        f"(Smart Exit: -{abs(diff_pct):.1f}% since)"
                        if current_price < row["Price"]
                        else f"(Missed Gains: +{diff_pct:.1f}% since)"
                    )
                elif row["Code"] == "P":
                    behavior_note = (
                        f"(Profitable Entry: +{diff_pct:.1f}%)"
                        if current_price > row["Price"]
                        else f"(Unrealized Loss: -{abs(diff_pct):.1f}%)"
                    )
            txn_list_str += (
                f"- {row['Date']}: {row['Person']} ({row['Role']}) "
                f"{row['Type']} {row['Amount']} shares at ${row['Price']:.2f} "
                f"(Code: {row['Code']}) {behavior_note}\n"
            )

        # ── Python-derived recommendation (single source of truth) ────────
        # Based on open-market P vs S only. F/A/C/G are excluded from signal.
        s_sold  = group[group["Code"] == "S"]["ActualSold"].sum()
        p_bought = group[group["Code"] == "P"]["ActualBought"].sum()
        net_open_market = p_bought - s_sold
        if p_bought > 0 and net_open_market > 0:
            recommendation = "BUY"
        elif p_bought > 0 and net_open_market < 0 and p_bought / max(s_sold, 1) > 0.3:
            recommendation = "HOLD/MIXED"   # some buying offsets heavy selling
        elif p_bought == 0 and s_sold == 0:
            recommendation = "NEUTRAL"
        else:
            recommendation = "HOLD/MIXED"   # selling present but routine for large-caps

        # ── LLM: analyst narrative only (no tables — Python owns those) ───
        system_prompt = (
            "You are a senior equity analyst writing a concise insider-trading "
            "commentary for an investment report. Write in a professional, "
            "first-person plural voice ('We note...', 'Our analysis shows...'). "
            "Do NOT reproduce any data tables — the client already has the full "
            "breakdown. Stick to interpretation only.\n\n"
            "IMPORTANT TONE RULES:\n"
            "1. Never characterise insider selling as 'negative', 'bearish', or a lack of confidence. "
            "Insiders sell for many personal reasons (diversification, liquidity needs, tax planning, "
            "pre-set trading plans) unrelated to company outlook. Describe activity neutrally.\n"
            "2. Do NOT provide any investment recommendation. Do NOT use the words buy, sell, hold, "
            "bullish, or bearish. Do NOT suggest what an investor should do.\n"
            "3. Close the commentary with the phrase: 'Investor discretion is advised.'"
        )
        user_prompt = f"""
Insider trading data for {issuer} ({ticker or 'Unknown Ticker'}):

Open-Market Bought (P): ${p_bought:,.2f} ({int(group[group['Code']=='P']['Amount'].sum()):,} shares)
Open-Market Sold   (S): ${s_sold:,.2f}   ({int(group[group['Code']=='S']['Amount'].sum()):,} shares)
Tax Withheld       (F): ${group[group['Code']=='F']['ActualSold'].sum():,.2f} (mandatory — exclude from signal)
Net Signal (P - S)    : ${net_open_market:,.2f}
{market_context}
Avg disposal price    : ${avg_disposed_price:,.2f}

Notable insider transactions (S-code only):
{txn_list_str[-3000:]}

Write a 2–3 paragraph professional commentary:
1. Describe the overall pattern of open-market P and S activity neutrally (ignore F/A/C/G).
2. Call out the most significant individual trades (names, amounts, dates) without implying intent.
3. Close with exactly: "Investor discretion is advised."

Be concise. No bullet lists. No headers. No tables. Maximum 200 words.
"""

        narrative = "Analysis unavailable (no API key configured)."
        if client:
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    max_tokens=400,
                    temperature=0.3,
                )
                narrative = response.choices[0].message.content.strip()
            except Exception as e:
                narrative = f"Error generating analysis: {e}"

        # Pre-computed per-code totals (Python truth — not from LLM)
        s_grp = group[group["Code"] == "S"]
        f_grp = group[group["Code"] == "F"]
        p_grp = group[group["Code"] == "P"]
        s_total = s_grp["ActualSold"].sum()
        f_total = f_grp["ActualSold"].sum()
        p_total = p_grp["ActualBought"].sum()
        s_shares = int(s_grp["Amount"].sum())
        f_shares = int(f_grp["Amount"].sum())
        p_shares = int(p_grp["Amount"].sum())

        # Key open-market trade dates (S and P only — the signal transactions)
        signal_dates = (
            group[group["Code"].isin(["S", "P"])]
            .groupby("Date")["Amount"]
            .sum()
            .sort_index(ascending=False)
        )
        top_dates = signal_dates.head(10)
        dates_str = "\n".join(
            f"  {date}: {int(shares):,} shares (open-market S/P)"
            for date, shares in top_dates.items()
        )

        report[issuer] = {
            "Recommendation": recommendation,
            "Reason": narrative,
            # Python-computed fields (guaranteed correct)
            "Net_Inside_Flow": actual_net_flow,
            "Net_Cash_Flow": net_cash_flow,
            "S_Total": s_total,           # open-market sale dollars
            "F_Total": f_total,           # tax-withholding dollars
            "P_Total": p_total,           # open-market purchase dollars
            "S_Shares": s_shares,
            "F_Shares": f_shares,
            "P_Shares": p_shares,
            "Total_Bought": buy_volume,
            "Total_Sold": sell_volume,
            "Transaction_Count": len(group),
            "Total_Acquired_Shares": total_acquired,
            "Total_Disposed_Shares": total_disposed,
            "Acquired_Txn_Count": acquired_txn_count,
            "Disposed_Txn_Count": disposed_txn_count,
            "Avg_Acquired_Price": avg_acquired_price,
            "Avg_Disposed_Price": avg_disposed_price,
            "Insider_Table": insider_table_str,   # pre-computed per-insider breakdown
            "Key_Trade_Dates": dates_str,          # top 10 S/P dates
            "Current_Price": current_price if isinstance(current_price, float) else None,
            "Details": group[["Date", "Person", "Role", "Code", "Amount", "Price", "Type"]].to_dict('records')
        }
        
    return report

# if __name__ == "__main__":
#     # Mock data for testing
#     mock_data = [
#         {
#             "issuer_name": "Test Corp",
#             "reporting_person_name": "CEO",
#             "relationship": ["Officer"],
#             "transactions": [
#                 {"date": "2026-01-01", "code": "P", "amount": "1000", "price": "100", "acquired_disposed": "A"},
#                 {"date": "2026-01-02", "code": "S", "amount": "500", "price": "110", "acquired_disposed": "D"}
#             ]
#         }
#     ]
#     import json
#     print(json.dumps(analyze_transactions(mock_data), indent=2))
