"""
Options chain analytics engine — pure deterministic Python, no LLM calls.

Metric strategy (per expiration):
  OI > 0  → use openInterest  (multi-day positional buildup)
  OI = 0  → use volume        (today's intraday flow)
Yahoo Finance only populates OI after EOD settlement; volume is always available.

Signals computed:
  - Volume-based P/C ratio and sentiment
  - Notional dollar flow (volume × lastPrice × 100) per strike — ranks trade SIZE not just count
  - IV skew (avg put IV − avg call IV) — positive = fear premium on puts
  - ATM concentration % — how much activity clusters within ±2% of current price
  - Support / resistance from top activity strikes below/above price
  - Unusual activity: volume spikes vs expiration mean
  - Smart money: long-dated OI when available (rare with Yahoo Finance)
"""
import traceback
from datetime import datetime, timezone, date
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


class OptionsAnalytics:

    UNUSUAL_VOL_PERCENTILE    = 85
    UNUSUAL_MIN_VOLUME        = 300
    SMART_MONEY_DTE_THRESHOLD  = 90
    SMART_MONEY_MIN_ABS_OI     = 1000   # raised: filter out tiny/fringe OI
    SMART_MONEY_MAX_STRIKE_PCT = 0.40   # strike must be within ±40% of current price
    BULLISH_PC_THRESHOLD      = 0.7
    BEARISH_PC_THRESHOLD      = 1.3
    TOP_N_STRIKES             = 5
    PRICE_WINDOW_PCT          = 0.20
    ATM_WINDOW_PCT            = 0.02     # ±2% for ATM concentration
    MAX_NEAR_TERM             = 4
    MAX_LONG_DATED            = 2
    MIN_IV                    = 0.01     # filter garbage 1e-05 IV values

    # ── Public API ──────────────────────────────────────────────────────────

    def analyze(self, ticker_symbol: str, expiration_date: Optional[str] = None) -> dict:
        errors = []
        ticker_symbol = ticker_symbol.upper().strip()

        try:
            tk = yf.Ticker(ticker_symbol)
        except Exception as e:
            return {"error": f"Cannot create ticker for {ticker_symbol}: {e}"}

        current_price = self._get_current_price(tk, errors)

        try:
            all_expirations = list(tk.options)
        except Exception as e:
            return {"error": f"No options data for {ticker_symbol}: {e}"}

        if not all_expirations:
            return {"error": f"No options expirations found for {ticker_symbol}"}

        today = datetime.now(timezone.utc).date()

        if expiration_date:
            if expiration_date not in all_expirations:
                return {
                    "error": f"Expiration '{expiration_date}' not available.",
                    "available_expirations": all_expirations[:10],
                }
            expirations_to_analyze = [expiration_date]
        else:
            expirations_to_analyze = self._select_expirations(all_expirations, today)

        all_calls, all_puts, per_exp_results = self._fetch_chains(
            tk, expirations_to_analyze, today, current_price, errors
        )

        if not all_calls:
            return {"error": "Failed to fetch any option chain data.", "details": errors}

        calls_df = pd.concat(all_calls, ignore_index=True)
        puts_df  = pd.concat(all_puts,  ignore_index=True)

        # ── Aggregate metrics ────────────────────────────────────────────────
        total_call_vol     = int(calls_df["volume"].sum())
        total_put_vol      = int(puts_df["volume"].sum())
        total_call_notional = int(calls_df["notional"].sum())
        total_put_notional  = int(puts_df["notional"].sum())
        total_call_oi      = int(calls_df["openInterest"].sum())
        total_put_oi       = int(puts_df["openInterest"].sum())

        # Prefer OI-based ratio if any expiration has OI; else volume
        if total_call_oi > 0:
            agg_metric   = "oi"
            agg_call_act = total_call_oi
            agg_put_act  = total_put_oi
            agg_pc_ratio = round(total_put_oi / total_call_oi, 4)
        elif total_call_vol > 0:
            agg_metric   = "volume"
            agg_call_act = total_call_vol
            agg_put_act  = total_put_vol
            agg_pc_ratio = round(total_put_vol / total_call_vol, 4)
        else:
            agg_metric = "none"; agg_call_act = 0; agg_put_act = 0; agg_pc_ratio = None

        sentiment = self._classify_sentiment(agg_pc_ratio)

        # ── Add activity + notional columns ──────────────────────────────────
        calls_df = self._add_activity_col(calls_df)
        puts_df  = self._add_activity_col(puts_df)

        total_call_activity = int(calls_df["activity"].sum())
        total_put_activity  = int(puts_df["activity"].sum())

        # ── IV skew — exclude DTE=0 (expiration day) to avoid noise ─────────────
        # On expiry day most IV values are garbage; only meaningful for DTE > 1
        valid_exp_for_iv = {
            r["expiration"] for r in per_exp_results if r["dte"] > 1
        }
        c_iv_df = calls_df[calls_df["expiration"].isin(valid_exp_for_iv)]
        p_iv_df = puts_df[ puts_df["expiration"].isin(valid_exp_for_iv)]
        c_iv_vals = c_iv_df[c_iv_df["impliedVolatility"] > self.MIN_IV]["impliedVolatility"]
        p_iv_vals = p_iv_df[p_iv_df["impliedVolatility"] > self.MIN_IV]["impliedVolatility"]
        avg_call_iv = round(float(c_iv_vals.mean()) * 100, 1) if len(c_iv_vals) else None
        avg_put_iv  = round(float(p_iv_vals.mean()) * 100, 1) if len(p_iv_vals) else None
        iv_skew     = round(avg_put_iv - avg_call_iv, 1) if (avg_call_iv and avg_put_iv) else None

        # ── ATM concentration ─────────────────────────────────────────────────
        atm_concentration = self._compute_atm_concentration(calls_df, puts_df, current_price)

        # ── Concentration zones ───────────────────────────────────────────────
        bullish_zones = self._top_activity_strikes(calls_df, "call", total_call_activity)
        bearish_zones = self._top_activity_strikes(puts_df,  "put",  total_put_activity)

        # ── Support / resistance ──────────────────────────────────────────────
        support_levels, resistance_levels = self._derive_levels(calls_df, puts_df, current_price)

        # ── Max pain (OI mode only) ───────────────────────────────────────────
        max_pain_list = [
            {"expiration": r["expiration"], "strike": r["max_pain_strike"]}
            for r in per_exp_results if r["max_pain_strike"] is not None
        ]

        # ── Smart money ───────────────────────────────────────────────────────
        smart_money = self._scan_smart_money(calls_df, puts_df, expirations_to_analyze, today)

        # ── Unusual activity ──────────────────────────────────────────────────
        unusual = self._detect_unusual_activity(calls_df, puts_df, per_exp_results)

        # ── Top notional trades (dollar flow ranking) ─────────────────────────
        top_notional_calls = self._top_notional_strikes(calls_df, "call", total_call_notional)
        top_notional_puts  = self._top_notional_strikes(puts_df,  "put",  total_put_notional)

        return {
            "ticker":         ticker_symbol,
            "current_price":  round(float(current_price), 2) if current_price else None,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "expirations_analyzed": expirations_to_analyze,
            "all_available_expirations": all_expirations,
            "per_expiration": per_exp_results,
            "aggregate": {
                "metric_used":           agg_metric,
                "put_call_ratio":        agg_pc_ratio,
                "sentiment":             sentiment,
                "call_activity":         agg_call_act,
                "put_activity":          agg_put_act,
                "total_call_volume":     total_call_vol,
                "total_put_volume":      total_put_vol,
                "total_call_notional_usd": total_call_notional,
                "total_put_notional_usd":  total_put_notional,
                "avg_call_iv_pct":       avg_call_iv,
                "avg_put_iv_pct":        avg_put_iv,
                "iv_skew_pct":           iv_skew,   # positive = puts more expensive = fear
                "atm_concentration":     atm_concentration,
                "atm_concentration_pct": round(
                    sum(v for v in [atm_concentration["atm_call_pct"], atm_concentration["atm_put_pct"]] if v is not None)
                    / max(1, sum(1 for v in [atm_concentration["atm_call_pct"], atm_concentration["atm_put_pct"]] if v is not None)),
                    1
                ),
            },
            "concentration_zones":  {"bullish": bullish_zones, "bearish": bearish_zones},
            "top_notional_flow":    {"calls": top_notional_calls, "puts": top_notional_puts},
            "support_levels":       support_levels,
            "resistance_levels":    resistance_levels,
            "max_pain":             max_pain_list,
            "smart_money":          smart_money,
            "unusual_activity":     unusual,
            "data_quality": {
                "expirations_requested":   len(expirations_to_analyze),
                "expirations_with_data":   len(per_exp_results),
                "expirations_oi_mode":     sum(1 for r in per_exp_results if r["metric_used"] == "oi"),
                "expirations_volume_mode": sum(1 for r in per_exp_results if r["metric_used"] == "volume"),
                "errors": errors,
            },
        }

    def get_expiration_dates_with_dte(self, ticker_symbol: str) -> dict:
        try:
            tk = yf.Ticker(ticker_symbol.upper().strip())
            expirations = tk.options
            today = datetime.now(timezone.utc).date()

            near_term, mid_term, long_dated = [], [], []
            for exp in expirations:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                entry = {"expiration": exp, "dte": dte}
                if dte <= 30:   near_term.append(entry)
                elif dte <= 90: mid_term.append(entry)
                else:           long_dated.append(entry)

            return {
                "ticker": ticker_symbol.upper(),
                "total_expirations": len(expirations),
                "near_term_le30":   near_term,
                "mid_term_31_90":   mid_term,
                "long_dated_gt90":  long_dated,
                "all_expirations":  list(expirations),
            }
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    def get_chain_for_chart(
        self, ticker_symbol: str, expiration_date: str,
        current_price: Optional[float] = None,
    ) -> dict:
        try:
            tk    = yf.Ticker(ticker_symbol.upper().strip())
            chain = tk.option_chain(expiration_date)
            calls = chain.calls.copy()
            puts  = chain.puts.copy()

            for df in [calls, puts]:
                df["openInterest"]      = pd.to_numeric(df.get("openInterest",      0), errors="coerce").fillna(0)
                df["volume"]            = pd.to_numeric(df.get("volume",            0), errors="coerce").fillna(0)
                df["strike"]            = pd.to_numeric(df.get("strike",            0), errors="coerce").fillna(0)
                df["lastPrice"]         = pd.to_numeric(df.get("lastPrice",         0), errors="coerce").fillna(0)
                df["impliedVolatility"] = pd.to_numeric(df.get("impliedVolatility", 0), errors="coerce").fillna(0)

            if current_price is None:
                current_price = self._get_current_price(tk, [])

            total_oi    = calls["openInterest"].sum() + puts["openInterest"].sum()
            metric_used = "oi" if total_oi > 0 else "volume"
            act_col     = "openInterest" if metric_used == "oi" else "volume"

            if current_price:
                # Tight ±10% window for the chart — avoids deep ITM/OTM dead zones
                lo = current_price * 0.90
                hi = current_price * 1.10
                calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)].copy()
                puts  = puts[ (puts["strike"]  >= lo) & (puts["strike"]  <= hi)].copy()

            # Notional per strike
            calls["notional"] = calls["volume"] * calls["lastPrice"] * 100
            puts["notional"]  = puts["volume"]  * puts["lastPrice"]  * 100

            call_act = calls[["strike", act_col, "notional"]].rename(
                columns={act_col: "call_activity", "notional": "call_notional"})
            put_act  = puts[["strike", act_col, "notional"]].rename(
                columns={act_col: "put_activity", "notional": "put_notional"})
            merged   = pd.merge(call_act, put_act, on="strike", how="outer").fillna(0).sort_values("strike")

            # Drop strikes where both calls AND puts have zero activity — pure noise
            merged = merged[(merged["call_activity"] > 0) | (merged["put_activity"] > 0)]

            total_call = merged["call_activity"].sum()
            total_put  = merged["put_activity"].sum()
            pc_ratio   = round(total_put / total_call, 4) if total_call > 0 else None
            max_pain   = self._calculate_max_pain(calls, puts) if metric_used == "oi" else None

            today_date = date.today()
            try:
                dte = (datetime.strptime(expiration_date, "%Y-%m-%d").date() - today_date).days
            except Exception:
                dte = None

            # Top strikes by activity for annotations
            top_calls = merged.nlargest(3, "call_activity")[["strike", "call_activity"]].to_dict("records")
            top_puts  = merged.nlargest(3, "put_activity")[["strike", "put_activity"]].to_dict("records")

            return {
                "ticker":           ticker_symbol.upper(),
                "expiration":       expiration_date,
                "dte":              dte,
                "metric_used":      metric_used,
                "current_price":    round(float(current_price), 2) if current_price else None,
                "max_pain":         max_pain,
                "strikes":          merged["strike"].tolist(),
                "call_activity":    merged["call_activity"].astype(int).tolist(),
                "put_activity":     merged["put_activity"].astype(int).tolist(),
                "call_notional":    (merged["call_notional"] / 1_000_000).round(3).tolist(),
                "put_notional":     (merged["put_notional"]  / 1_000_000).round(3).tolist(),
                "put_call_ratio":   pc_ratio,
                "top_call_strikes": top_calls,
                "top_put_strikes":  top_puts,
            }
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get_current_price(self, tk, errors):
        try:
            info  = tk.info
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask")
            if price: return float(price)
            hist = tk.history(period="1d")
            if not hist.empty: return float(hist["Close"].iloc[-1])
        except Exception as e:
            errors.append(f"Price fetch: {e}")
        return None

    def _select_expirations(self, all_expirations, today):
        exp_with_dte = []
        for exp in all_expirations:
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
                if dte >= 0:
                    exp_with_dte.append((exp, dte))
            except Exception:
                pass
        near_term  = [e for e, d in exp_with_dte if d <= self.SMART_MONEY_DTE_THRESHOLD][: self.MAX_NEAR_TERM]
        long_dated = [e for e, d in exp_with_dte if d  > self.SMART_MONEY_DTE_THRESHOLD][: self.MAX_LONG_DATED]
        result = near_term + long_dated
        return result or [all_expirations[0]]

    def _fetch_chains(self, tk, expirations, today, current_price, errors):
        all_calls, all_puts, per_exp_results = [], [], []

        for exp in expirations:
            try:
                chain = tk.option_chain(exp)
                calls = chain.calls.copy()
                puts  = chain.puts.copy()

                for df in [calls, puts]:
                    df["openInterest"]      = pd.to_numeric(df.get("openInterest",      0), errors="coerce").fillna(0)
                    df["volume"]            = pd.to_numeric(df.get("volume",            0), errors="coerce").fillna(0)
                    df["strike"]            = pd.to_numeric(df.get("strike",            0), errors="coerce").fillna(0)
                    df["lastPrice"]         = pd.to_numeric(df.get("lastPrice",         0), errors="coerce").fillna(0)
                    df["impliedVolatility"] = pd.to_numeric(df.get("impliedVolatility", 0), errors="coerce").fillna(0)
                    # Notional flow = volume × lastPrice × 100 (dollar value of traded contracts)
                    df["notional"] = df["volume"] * df["lastPrice"] * 100

                calls["expiration"] = exp
                puts["expiration"]  = exp

                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days

                total_oi    = int(calls["openInterest"].sum() + puts["openInterest"].sum())
                metric_used = "oi" if total_oi > 0 else "volume"
                act_col     = "openInterest" if metric_used == "oi" else "volume"

                call_act      = int(calls[act_col].sum())
                put_act       = int(puts[act_col].sum())
                call_vol      = int(calls["volume"].sum())
                put_vol       = int(puts["volume"].sum())
                call_notional = int(calls["notional"].sum())
                put_notional  = int(puts["notional"].sum())

                pc = round(put_act / call_act, 4) if call_act > 0 else None

                # IV — only meaningful when DTE > 1 (expiry-day IV is noise)
                if dte > 1:
                    c_iv = calls[calls["impliedVolatility"] > self.MIN_IV]["impliedVolatility"]
                    p_iv = puts[ puts["impliedVolatility"]  > self.MIN_IV]["impliedVolatility"]
                    avg_c_iv = round(float(c_iv.mean()) * 100, 1) if len(c_iv) else None
                    avg_p_iv = round(float(p_iv.mean()) * 100, 1) if len(p_iv) else None
                    iv_skew  = round(avg_p_iv - avg_c_iv, 1) if (avg_c_iv and avg_p_iv) else None
                else:
                    avg_c_iv = avg_p_iv = iv_skew = None

                # ATM concentration for this expiration
                atm = self._compute_atm_concentration_exp(calls, puts, current_price)

                max_pain_s = self._calculate_max_pain(calls, puts) if metric_used == "oi" else None

                # Top 3 strikes by notional for quick per-exp insight
                top_c_notional = self._exp_top_notional(calls, "call", 3)
                top_p_notional = self._exp_top_notional(puts,  "put",  3)

                per_exp_results.append({
                    "expiration":          exp,
                    "dte":                 dte,
                    "metric_used":         metric_used,
                    "call_activity":       call_act,
                    "put_activity":        put_act,
                    "call_volume":         call_vol,
                    "put_volume":          put_vol,
                    "call_notional_usd":   call_notional,
                    "put_notional_usd":    put_notional,
                    "put_call_ratio":      pc,
                    "avg_call_iv_pct":     avg_c_iv,
                    "avg_put_iv_pct":      avg_p_iv,
                    "iv_skew_pct":         iv_skew,
                    "atm_call_vol_pct":    atm["atm_call_pct"],
                    "atm_put_vol_pct":     atm["atm_put_pct"],
                    "max_pain_strike":     max_pain_s,
                    "top_call_notional":   top_c_notional,
                    "top_put_notional":    top_p_notional,
                })

                all_calls.append(calls)
                all_puts.append(puts)

            except Exception as e:
                errors.append(f"Expiration {exp}: {e}")

        return all_calls, all_puts, per_exp_results

    def _add_activity_col(self, df):
        df = df.copy()
        df["activity"] = df.apply(
            lambda r: r["openInterest"] if r["openInterest"] > 0 else r["volume"], axis=1
        )
        return df

    def _calculate_max_pain(self, calls, puts):
        try:
            all_strikes = sorted(set(calls["strike"].unique()) | set(puts["strike"].unique()))
            if not all_strikes: return None
            min_pain, mp_strike = float("inf"), None
            for k in all_strikes:
                cp = float(((calls["strike"] - k).clip(lower=0) * calls["openInterest"]).sum())
                pp = float(((k - puts["strike"]).clip(lower=0) * puts["openInterest"]).sum())
                if (cp + pp) < min_pain:
                    min_pain = cp + pp
                    mp_strike = float(k)
            return mp_strike
        except Exception:
            return None

    def _classify_sentiment(self, pc_ratio):
        if pc_ratio is None:  return "UNKNOWN"
        if pc_ratio < self.BULLISH_PC_THRESHOLD: return "BULLISH"
        if pc_ratio > self.BEARISH_PC_THRESHOLD: return "BEARISH"
        return "NEUTRAL"

    def _compute_atm_concentration(self, calls_df, puts_df, current_price):
        """% of total call/put volume that falls within ±ATM_WINDOW_PCT of current price."""
        if current_price is None:
            return {"atm_call_pct": None, "atm_put_pct": None}
        lo = current_price * (1 - self.ATM_WINDOW_PCT)
        hi = current_price * (1 + self.ATM_WINDOW_PCT)
        c_total = calls_df["volume"].sum()
        p_total = puts_df["volume"].sum()
        c_atm   = calls_df[(calls_df["strike"] >= lo) & (calls_df["strike"] <= hi)]["volume"].sum()
        p_atm   = puts_df[ (puts_df["strike"]  >= lo) & (puts_df["strike"]  <= hi)]["volume"].sum()
        return {
            "atm_call_pct": round(100 * c_atm / c_total, 1) if c_total > 0 else None,
            "atm_put_pct":  round(100 * p_atm / p_total, 1) if p_total > 0 else None,
        }

    def _compute_atm_concentration_exp(self, calls, puts, current_price):
        if current_price is None:
            return {"atm_call_pct": None, "atm_put_pct": None}
        lo = current_price * (1 - self.ATM_WINDOW_PCT)
        hi = current_price * (1 + self.ATM_WINDOW_PCT)
        c_total = calls["volume"].sum()
        p_total = puts["volume"].sum()
        c_atm   = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]["volume"].sum()
        p_atm   = puts[ (puts["strike"]  >= lo) & (puts["strike"]  <= hi)]["volume"].sum()
        return {
            "atm_call_pct": round(100 * c_atm / c_total, 1) if c_total > 0 else None,
            "atm_put_pct":  round(100 * p_atm / p_total, 1) if p_total > 0 else None,
        }

    def _top_activity_strikes(self, df, option_type, total_activity):
        """Top (strike, expiration) pairs by activity — each expiry treated separately."""
        if df.empty:
            return []
        grouped = (
            df.groupby(["strike", "expiration"])
            .agg(
                activity=("activity", "sum"),
                notional=("notional", "sum"),
                openInterest=("openInterest", "first"),
            )
            .reset_index()
            .sort_values("activity", ascending=False)
            .head(self.TOP_N_STRIKES)
        )
        result = []
        for _, row in grouped.iterrows():
            if row["activity"] == 0:
                continue
            metric = "oi" if row["openInterest"] > 0 else "volume"
            pct    = round(100 * row["activity"] / total_activity, 1) if total_activity > 0 else 0
            result.append({
                "strike":                  float(row["strike"]),
                "expiration":              row["expiration"],
                "metric_used":             metric,
                f"{option_type}_activity": int(row["activity"]),
                "notional_usd":            int(row["notional"]),
                "pct_of_total":            pct,
            })
        return result

    def _top_notional_strikes(self, df, option_type, total_notional):
        """Top (strike, expiration) pairs ranked by dollar flow — each expiry treated separately."""
        if df.empty:
            return []
        # Rank each unique (strike, expiration) contract independently
        grouped = (
            df.groupby(["strike", "expiration"])
            .agg(notional=("notional", "sum"), volume=("volume", "sum"),
                 lastPrice=("lastPrice", "first"), impliedVolatility=("impliedVolatility", "first"))
            .reset_index()
            .sort_values("notional", ascending=False)
            .head(self.TOP_N_STRIKES)
        )
        result = []
        for _, row in grouped.iterrows():
            if row["notional"] == 0:
                continue
            iv  = round(float(row["impliedVolatility"]) * 100, 1) if row["impliedVolatility"] > self.MIN_IV else None
            pct = round(100 * row["notional"] / total_notional, 1) if total_notional > 0 else 0
            result.append({
                "strike":                      float(row["strike"]),
                "expiration":                  row["expiration"],
                f"{option_type}_notional_usd": int(row["notional"]),
                "volume":                      int(row["volume"]),
                "last_price":                  round(float(row["lastPrice"]), 2),
                "iv_pct":                      iv,
                "pct_of_total_flow":           pct,
            })
        return result

    def _exp_top_notional(self, df, option_type, n):
        """Top N strikes by notional within a single expiration."""
        top = df.nlargest(n, "notional")
        result = []
        for _, row in top.iterrows():
            if row["notional"] == 0: continue
            iv = round(float(row["impliedVolatility"]) * 100, 1) if row["impliedVolatility"] > self.MIN_IV else None
            result.append({
                "strike":      float(row["strike"]),
                "volume":      int(row["volume"]),
                "notional_usd": int(row["notional"]),
                "last_price":  round(float(row["lastPrice"]), 2),
                "iv_pct":      iv,
                "itm":         bool(row.get("inTheMoney", False)),
            })
        return result

    def _derive_levels(self, calls_df, puts_df, current_price):
        # Intentionally aggregate across expirations: a strike with heavy activity
        # in multiple expirations is a stronger level, not double-counted noise.
        if current_price is None: return [], []
        below = puts_df[ puts_df["strike"]  < current_price]
        above = calls_df[calls_df["strike"] > current_price]
        support    = below.groupby("strike")["activity"].sum().sort_values(ascending=False).head(3).index.tolist()
        resistance = above.groupby("strike")["activity"].sum().sort_values(ascending=False).head(3).index.tolist()
        return (sorted([float(s) for s in support], reverse=True),
                sorted([float(s) for s in resistance]))

    def _scan_smart_money(self, calls_df, puts_df, expirations, today):
        # Volume-only scan — OI is always 0 from Yahoo Finance free tier.
        # Long-dated options trade fewer contracts/day so thresholds are lower.
        VOLUME_MIN = 50   # minimum volume at a single strike to qualify
        VOLUME_PCT = 85   # top 15% of strikes by volume within the expiration

        current_price = None
        if "strike" in calls_df.columns and not calls_df.empty:
            try:
                current_price = float(
                    (calls_df["strike"] * calls_df["activity"]).sum() / calls_df["activity"].sum()
                )
            except Exception:
                pass

        signals = []
        for exp in expirations:
            exp_date  = datetime.strptime(exp, "%Y-%m-%d").date()
            dte       = (exp_date - today).days
            if dte <= self.SMART_MONEY_DTE_THRESHOLD:
                continue
            exp_calls = calls_df[calls_df["expiration"] == exp]
            exp_puts  = puts_df[ puts_df["expiration"]  == exp]

            all_vols  = pd.concat([exp_calls["volume"], exp_puts["volume"]])
            if all_vols.sum() == 0:
                continue
            threshold = float(np.percentile(all_vols[all_vols > 0], VOLUME_PCT)) if (all_vols > 0).any() else 0.0

            for opt_type, df in [("call", exp_calls), ("put", exp_puts)]:
                for _, row in df.iterrows():
                    vol    = row["volume"]
                    strike = float(row["strike"])
                    if vol < VOLUME_MIN or vol < threshold:
                        continue
                    if current_price and abs(strike - current_price) / current_price > self.SMART_MONEY_MAX_STRIKE_PCT:
                        continue
                    signals.append({
                        "type":         opt_type,
                        "expiration":   exp,
                        "dte":          dte,
                        "strike":       strike,
                        "volume":       int(vol),
                        "notional_usd": int(row["notional"]),
                        "iv_pct":       round(float(row["impliedVolatility"]) * 100, 1) if row["impliedVolatility"] > self.MIN_IV else None,
                    })

        assessment = "INSUFFICIENT_DATA"
        dominant_strike = None
        if signals:
            sm_call = sum(s["volume"] for s in signals if s["type"] == "call")
            sm_put  = sum(s["volume"] for s in signals if s["type"] == "put")
            if   sm_call > sm_put * 1.5:  assessment = "ACCUMULATING"
            elif sm_put  > sm_call * 1.5: assessment = "HEDGING"
            else:                          assessment = "MIXED"
            dominant_strike = max(signals, key=lambda x: x["notional_usd"])["strike"]

        return {"signals": signals[:10], "assessment": assessment, "dominant_long_dated_strike": dominant_strike}

    def _detect_unusual_activity(self, calls_df, puts_df, per_exp_results):
        exp_metric = {r["expiration"]: r["metric_used"] for r in per_exp_results}
        unusual    = []

        for opt_type, df in [("call", calls_df), ("put", puts_df)]:
            for exp, grp in df.groupby("expiration"):
                metric = exp_metric.get(exp, "volume")
                if metric == "oi":
                    for _, row in grp.iterrows():
                        oi = row["openInterest"]; vol = row["volume"]
                        if vol >= 500 and oi > 0 and vol >= 3.0 * oi:
                            unusual.append({
                                "type": opt_type, "strike": float(row["strike"]),
                                "expiration": exp, "volume": int(vol),
                                "open_interest": int(oi), "notional_usd": int(row["notional"]),
                                "signal": "vol_oi_ratio", "ratio": round(vol / oi, 2),
                            })
                else:
                    vols = grp["volume"]
                    if vols.empty or vols.sum() == 0: continue
                    pos_vols = vols[vols > 0]
                    if pos_vols.empty: continue
                    threshold = float(np.percentile(pos_vols, self.UNUSUAL_VOL_PERCENTILE))
                    for _, row in grp.iterrows():
                        vol = row["volume"]
                        if vol >= threshold and vol >= self.UNUSUAL_MIN_VOLUME:
                            unusual.append({
                                "type": opt_type, "strike": float(row["strike"]),
                                "expiration": exp, "volume": int(vol),
                                "open_interest": int(row["openInterest"]),
                                "notional_usd": int(row["notional"]),
                                "last_price": round(float(row["lastPrice"]), 2),
                                "iv_pct": round(float(row["impliedVolatility"]) * 100, 1) if row["impliedVolatility"] > self.MIN_IV else None,
                                "signal": "volume_spike",
                                "ratio": round(vol / threshold, 2) if threshold > 0 else None,
                            })

        unusual.sort(key=lambda x: x.get("notional_usd", 0), reverse=True)
        return unusual[:15]
