
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
                price = float(txn["price"].replace('$', '').replace(',', ''))
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
        
        # Prepare transaction list string
        txn_list_str = ""
        # Sort by date for narrative flow
        sorted_group = group.sort_values("Date")
        
        for _, row in sorted_group.iterrows():
            txn_list_str += f"- {row['Date']}: {row['Person']} ({row['Role']}) {row['Type']} {row['Amount']} shares at ${row['Price']} (Code: {row['Code']})\n"
            
        # Fetch Market Data
        # We assume one ticker per issuer group (usually true)
        current_price = "N/A"
        ticker = group["Ticker"].iloc[0]
        
        market_context = ""
        
        if ticker:
            try:
                # remove brackets if still there or just take the code
                clean_ticker = ticker.strip()
                stock = yf.Ticker(clean_ticker)
                
                # fast way to get current price
                hist = stock.history(period="1d")
                if not hist.empty:
                    current_price = hist["Close"].iloc[-1]
                    market_context = f"- Current Market Price: ${current_price:,.2f}"
            except Exception as e:
                market_context = f"- Market Data Error: {e}"

        # Prepare transaction list string with behavioral context
        txn_list_str = ""
        # Sort by date for narrative flow
        sorted_group = group.sort_values("Date")
        
        for _, row in sorted_group.iterrows():
            behavior_note = ""
            if isinstance(current_price, (int, float)) and row['Price'] > 0:
                diff_pct = ((current_price - row['Price']) / row['Price']) * 100
                if row['Code'] in ['S', 'D']: # Sold
                    # If sold and price is now lower -> Good timing (Saved loss/locked profit)
                    # If sold and price is now higher -> Missed gains
                    if current_price < row['Price']:
                        behavior_note = f"(Smart Exit: Stock dropped {abs(diff_pct):.1f}% since)"
                    else:
                        behavior_note = f"(Missed Gains: Stock rose {diff_pct:.1f}% since)"
                elif row['Code'] in ['P', 'A']: # Bought
                    # If bought and price is now higher -> Good entry
                    if current_price > row['Price']:
                        behavior_note = f"(Profitable Entry: Up {diff_pct:.1f}%)"
                    else:
                        behavior_note = f"(Unrealized Loss: Down {abs(diff_pct):.1f}%)"
                        
            txn_list_str += f"- {row['Date']}: {row['Person']} ({row['Role']}) {row['Type']} {row['Amount']} shares at ${row['Price']} (Code: {row['Code']}) {behavior_note}\n"
            
        # Construct Prompt
        system_prompt = "You are an expert financial analyst. You must output your report EXACTLY in the specified format, with no additional conversational text or greetings."
        user_prompt = f"""
        Analyze the following insider trading activity for {issuer} ({ticker if ticker else 'Unknown Ticker'}).
        
        Overview:
        - Transaction Count: {txn_count}
        
        Acquired Metrics:
        - Total Acquired (Shares): {total_acquired:,.0f}
        - Total Acquired (Dollars): ${buy_volume:,.2f}
        - Transactions (Acquired): {acquired_txn_count}
        - Average Acquired Price: ${avg_acquired_price:,.2f}
        
        Disposed Metrics:
        - Total Disposed (Shares): {total_disposed:,.0f}
        - Total Disposed (Dollars): ${sell_volume:,.2f}
        - Transactions (Disposed): {disposed_txn_count}
        - Average Disposed Price: ${avg_disposed_price:,.2f}
        
        {market_context}
        
        Detailed Transactions:
        {txn_list_str}
        
        You must reply STRICTLY using the exact following format, including the blank lines for spacing:
        
        Summary
        
        - <Provide a bullet point briefly summarizing the total sales/buys, and average prices compared to market>
        
        - <Another bullet point with relevant transaction metrics>
        
        - <Another bullet point indicating whether it's mostly sales or buys>
        
        Recommendation
        
        - <BUY, SELL, or HOLD/MIXED>.
        
          - Rationale: <1-2 sentences explaining why based on the behavior (e.g., routine liquidity, panic selling, contrarian buying)>
          
          - For prospective buyers: <1 sentence with actionable advice for buyers>
        """
        
        narrative = "AI Analysis Unsupported (No API Key)"
        recommendation = "UNKNOWN"
        
        if client:
            try:
                response = client.chat.completions.create(
                    model="gpt-5-mini", # Requesting user's preferred model
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    # Fallback model in case gpt-5-mini isn't valid yet, usually handled by API error but here we assume it works or user has access.
                    # In a real scenario, we might catch the error and retry with gpt-4o-mini.
                )
                narrative = response.choices[0].message.content
                
                # Extract recommendation from narrative if possible, or just leave it embedded
                if "BUY" in narrative.upper() and "SELL" not in narrative.upper(): recommendation = "BUY"
                elif "SELL" in narrative.upper() and "BUY" not in narrative.upper(): recommendation = "SELL"
                else: recommendation = "HOLD/MIXED" # Simple heuristic extraction
                
            except Exception as e:
                narrative = f"Error generating analysis: {e}"
        else:
            # Fallback to heuristic if no key
            if total_signal > 100000: recommendation = "BUY"
            elif total_signal < -100000: recommendation = "SELL"
            else: recommendation = "HOLD"
            narrative = f"Automatic analysis (No API Key): Net flow is ${total_signal:,.2f}. Recommendation based on threshold."

        report[issuer] = {
            "Recommendation": recommendation,
            "Reason": narrative, # Now holds the full AI narrative
            "Net_Inside_Flow": actual_net_flow,
            "Net_Cash_Flow": net_cash_flow,
            "Total_Bought": buy_volume,
            "Total_Sold": sell_volume,
            "Transaction_Count": len(group),
            "Total_Acquired_Shares": total_acquired,
            "Total_Disposed_Shares": total_disposed,
            "Acquired_Txn_Count": acquired_txn_count,
            "Disposed_Txn_Count": disposed_txn_count,
            "Avg_Acquired_Price": avg_acquired_price,
            "Avg_Disposed_Price": avg_disposed_price,
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
