"""
Options chain analytics engine — pure deterministic Python, no LLM calls.

Metric strategy (per expiration):
  OI > 0  → use openInterest  (multi-day positional buildup)
  OI = 0  → use volume        (today's intraday flow)
Yahoo Finance only populates OI after end-of-day settlement; it is reliably
non-zero only for monthly expirations that have been active 1+ days.
"""
import traceback
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


class OptionsAnalytics:

    UNUSUAL_VOLUME_OI_RATIO   = 3.0     # vol/OI threshold in OI mode
    UNUSUAL_MIN_VOLUME        = 500     # absolute floor for unusual activity
    UNUSUAL_VOL_PERCENTILE    = 90      # percentile threshold in volume mode
    SMART_MONEY_DTE_THRESHOLD = 90
    SMART_MONEY_MIN_ABS_OI    = 500
    BULLISH_PC_THRESHOLD      = 0.7
    BEARISH_PC_THRESHOLD      = 1.3
    TOP_N_STRIKES             = 5
    PRICE_WINDOW_PCT          = 0.20    # ±20% window for chart / level derivation
    MAX_NEAR_TERM             = 4       # near-term expirations to include
    MAX_LONG_DATED            = 2       # monthly expirations (DTE>90) to include

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
            tk, expirations_to_analyze, today, errors
        )

        if not all_calls:
            return {"error": "Failed to fetch any option chain data.", "details": errors}

        calls_df = pd.concat(all_calls, ignore_index=True)
        puts_df  = pd.concat(all_puts,  ignore_index=True)

        # Aggregate put/call ratio — prefer OI when any expiration has it
        total_call_oi  = int(calls_df["openInterest"].sum())
        total_put_oi   = int(puts_df["openInterest"].sum())
        total_call_vol = int(calls_df["volume"].sum())
        total_put_vol  = int(puts_df["volume"].sum())

        if total_call_oi > 0:
            agg_metric     = "oi"
            agg_call_act   = total_call_oi
            agg_put_act    = total_put_oi
            agg_pc_ratio   = round(total_put_oi / total_call_oi, 4)
        elif total_call_vol > 0:
            agg_metric     = "volume"
            agg_call_act   = total_call_vol
            agg_put_act    = total_put_vol
            agg_pc_ratio   = round(total_put_vol / total_call_vol, 4)
        else:
            agg_metric     = "none"
            agg_call_act   = 0
            agg_put_act    = 0
            agg_pc_ratio   = None

        sentiment = self._classify_sentiment(agg_pc_ratio)

        # Add activity column to each df for downstream functions
        calls_df = self._add_activity_col(calls_df)
        puts_df  = self._add_activity_col(puts_df)

        total_call_activity = int(calls_df["activity"].sum())
        total_put_activity  = int(puts_df["activity"].sum())
        bullish_zones    = self._top_activity_strikes(calls_df, "call", total_call_activity)
        bearish_zones    = self._top_activity_strikes(puts_df,  "put",  total_put_activity)
        support_levels, resistance_levels = self._derive_levels(calls_df, puts_df, current_price)

        max_pain_list = [
            {"expiration": r["expiration"], "strike": r["max_pain_strike"]}
            for r in per_exp_results
            if r["max_pain_strike"] is not None
        ]

        smart_money = self._scan_smart_money(calls_df, puts_df, expirations_to_analyze, today)
        unusual     = self._detect_unusual_activity(calls_df, puts_df, per_exp_results)

        return {
            "ticker":           ticker_symbol,
            "current_price":    round(float(current_price), 2) if current_price else None,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "expirations_analyzed": expirations_to_analyze,
            "all_available_expirations": all_expirations,
            "per_expiration":   per_exp_results,
            "aggregate": {
                "metric_used":   agg_metric,
                "put_call_ratio": agg_pc_ratio,
                "sentiment":     sentiment,
                "call_activity": agg_call_act,
                "put_activity":  agg_put_act,
                "sentiment_thresholds": {
                    "bullish_below": self.BULLISH_PC_THRESHOLD,
                    "bearish_above": self.BEARISH_PC_THRESHOLD,
                },
            },
            "concentration_zones": {"bullish": bullish_zones, "bearish": bearish_zones},
            "support_levels":    support_levels,
            "resistance_levels": resistance_levels,
            "max_pain":          max_pain_list,
            "smart_money":       smart_money,
            "unusual_activity":  unusual,
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
                if dte <= 30:
                    near_term.append(entry)
                elif dte <= 90:
                    mid_term.append(entry)
                else:
                    long_dated.append(entry)

            return {
                "ticker":            ticker_symbol.upper(),
                "total_expirations": len(expirations),
                "near_term_le30":    near_term,
                "mid_term_31_90":    mid_term,
                "long_dated_gt90":   long_dated,
                "all_expirations":   list(expirations),
            }
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    def get_chain_for_chart(
        self,
        ticker_symbol: str,
        expiration_date: str,
        current_price: Optional[float] = None,
    ) -> dict:
        """
        Return per-strike activity data for the visualization layer.
        Uses OI when populated, volume otherwise.
        Filtered to current_price ± PRICE_WINDOW_PCT for chart readability.
        """
        try:
            tk    = yf.Ticker(ticker_symbol.upper().strip())
            chain = tk.option_chain(expiration_date)
            calls = chain.calls.copy()
            puts  = chain.puts.copy()

            for df in [calls, puts]:
                df["openInterest"] = pd.to_numeric(df.get("openInterest", 0), errors="coerce").fillna(0)
                df["volume"]       = pd.to_numeric(df.get("volume",       0), errors="coerce").fillna(0)
                df["strike"]       = pd.to_numeric(df.get("strike",       0), errors="coerce").fillna(0)

            if current_price is None:
                current_price = self._get_current_price(tk, [])

            # Determine metric for this expiration
            total_oi = calls["openInterest"].sum() + puts["openInterest"].sum()
            metric_used = "oi" if total_oi > 0 else "volume"
            act_col = "openInterest" if metric_used == "oi" else "volume"

            # Filter to price window
            if current_price:
                lo = current_price * (1 - self.PRICE_WINDOW_PCT)
                hi = current_price * (1 + self.PRICE_WINDOW_PCT)
                calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
                puts  = puts[ (puts["strike"]  >= lo) & (puts["strike"]  <= hi)]

            call_act = calls[["strike", act_col]].rename(columns={act_col: "call_activity"})
            put_act  = puts[ ["strike", act_col]].rename(columns={act_col: "put_activity"})
            merged   = pd.merge(call_act, put_act, on="strike", how="outer").fillna(0)
            merged   = merged.sort_values("strike")

            total_call = merged["call_activity"].sum()
            total_put  = merged["put_activity"].sum()
            pc_ratio   = round(total_put / total_call, 4) if total_call > 0 else None

            # Max pain only meaningful in OI mode
            max_pain = self._calculate_max_pain(calls, puts) if metric_used == "oi" else None

            return {
                "ticker":         ticker_symbol.upper(),
                "expiration":     expiration_date,
                "metric_used":    metric_used,
                "current_price":  round(float(current_price), 2) if current_price else None,
                "max_pain":       max_pain,
                "strikes":        merged["strike"].tolist(),
                "call_activity":  merged["call_activity"].astype(int).tolist(),
                "put_activity":   merged["put_activity"].astype(int).tolist(),
                "put_call_ratio": pc_ratio,
            }
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get_current_price(self, tk: yf.Ticker, errors: list) -> Optional[float]:
        try:
            info  = tk.info
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask")
            if price:
                return float(price)
            hist = tk.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            errors.append(f"Price fetch: {e}")
        return None

    def _select_expirations(self, all_expirations: list, today) -> list:
        exp_with_dte = []
        for exp in all_expirations:
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
                if dte > 0:
                    exp_with_dte.append((exp, dte))
            except Exception:
                pass

        near_term  = [e for e, d in exp_with_dte if d <= self.SMART_MONEY_DTE_THRESHOLD][: self.MAX_NEAR_TERM]
        long_dated = [e for e, d in exp_with_dte if d > self.SMART_MONEY_DTE_THRESHOLD][: self.MAX_LONG_DATED]
        result = near_term + long_dated
        return result or [all_expirations[0]]

    def _fetch_chains(self, tk: yf.Ticker, expirations: list, today, errors: list):
        all_calls, all_puts, per_exp_results = [], [], []

        for exp in expirations:
            try:
                chain = tk.option_chain(exp)
                calls = chain.calls.copy()
                puts  = chain.puts.copy()

                for df in [calls, puts]:
                    df["openInterest"]    = pd.to_numeric(df.get("openInterest",    0), errors="coerce").fillna(0)
                    df["volume"]          = pd.to_numeric(df.get("volume",          0), errors="coerce").fillna(0)
                    df["strike"]          = pd.to_numeric(df.get("strike",          0), errors="coerce").fillna(0)
                    df["impliedVolatility"] = pd.to_numeric(df.get("impliedVolatility", 0), errors="coerce").fillna(0)

                calls["expiration"] = exp
                puts["expiration"]  = exp

                # Decide metric for this expiration
                total_oi = int(calls["openInterest"].sum() + puts["openInterest"].sum())
                metric_used = "oi" if total_oi > 0 else "volume"
                act_col = "openInterest" if metric_used == "oi" else "volume"

                call_act = int(calls[act_col].sum())
                put_act  = int(puts[act_col].sum())
                pc       = round(put_act / call_act, 4) if call_act > 0 else None

                # Max pain only in OI mode
                max_pain_strike = self._calculate_max_pain(calls, puts) if metric_used == "oi" else None

                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days

                per_exp_results.append({
                    "expiration":      exp,
                    "dte":             dte,
                    "metric_used":     metric_used,
                    "call_activity":   call_act,
                    "put_activity":    put_act,
                    "put_call_ratio":  pc,
                    "max_pain_strike": max_pain_strike,
                })

                all_calls.append(calls)
                all_puts.append(puts)

            except Exception as e:
                errors.append(f"Expiration {exp}: {e}")

        return all_calls, all_puts, per_exp_results

    def _add_activity_col(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add an 'activity' column: openInterest where available, volume otherwise.
        Applied per-row so mixed expirations within the same DataFrame are handled correctly.
        """
        df = df.copy()
        df["activity"] = df.apply(
            lambda row: row["openInterest"] if row["openInterest"] > 0 else row["volume"],
            axis=1,
        )
        return df

    def _calculate_max_pain(self, calls: pd.DataFrame, puts: pd.DataFrame) -> Optional[float]:
        try:
            all_strikes = sorted(set(calls["strike"].unique()) | set(puts["strike"].unique()))
            if not all_strikes:
                return None
            min_pain, max_pain_strike = float("inf"), None
            for k in all_strikes:
                call_pain = float(((calls["strike"] - k).clip(lower=0) * calls["openInterest"]).sum())
                put_pain  = float(((k - puts["strike"]).clip(lower=0) * puts["openInterest"]).sum())
                total = call_pain + put_pain
                if total < min_pain:
                    min_pain = total
                    max_pain_strike = float(k)
            return max_pain_strike
        except Exception:
            return None

    def _classify_sentiment(self, pc_ratio: Optional[float]) -> str:
        if pc_ratio is None:
            return "UNKNOWN"
        if pc_ratio < self.BULLISH_PC_THRESHOLD:
            return "BULLISH"
        if pc_ratio > self.BEARISH_PC_THRESHOLD:
            return "BEARISH"
        return "NEUTRAL"

    def _top_activity_strikes(self, df: pd.DataFrame, option_type: str, total_activity: int) -> list:
        grouped = (
            df.groupby("strike")["activity"]
            .sum()
            .sort_values(ascending=False)
            .head(self.TOP_N_STRIKES)
        )
        result = []
        for strike, act in grouped.items():
            if act == 0:
                continue
            best_row   = df[df["strike"] == strike].sort_values("activity", ascending=False)
            expiration = best_row.iloc[0]["expiration"] if not best_row.empty else "N/A"
            metric     = best_row.iloc[0].get("metric_used", "unknown") if "metric_used" in best_row.columns else (
                "oi" if best_row.iloc[0]["openInterest"] > 0 else "volume"
            )
            pct = round(100 * act / total_activity, 2) if total_activity > 0 else 0
            result.append({
                "strike":         float(strike),
                "expiration":     expiration,
                "metric_used":    metric,
                f"{option_type}_activity": int(act),
                "pct_of_total":   pct,
            })
        return result

    def _derive_levels(self, calls_df: pd.DataFrame, puts_df: pd.DataFrame, current_price: Optional[float]):
        if current_price is None:
            return [], []

        below = puts_df[puts_df["strike"] < current_price]
        above = calls_df[calls_df["strike"] > current_price]

        support    = below.groupby("strike")["activity"].sum().sort_values(ascending=False).head(3).index.tolist()
        resistance = above.groupby("strike")["activity"].sum().sort_values(ascending=False).head(3).index.tolist()

        return (
            sorted([float(s) for s in support], reverse=True),
            sorted([float(s) for s in resistance]),
        )

    def _scan_smart_money(self, calls_df: pd.DataFrame, puts_df: pd.DataFrame, expirations: list, today) -> dict:
        """Only scans expirations with DTE > 90 AND where OI > 0."""
        signals = []

        for exp in expirations:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if dte <= self.SMART_MONEY_DTE_THRESHOLD:
                continue

            exp_calls = calls_df[calls_df["expiration"] == exp]
            exp_puts  = puts_df[ puts_df["expiration"]  == exp]

            # Only proceed if OI is populated for this expiration
            total_oi = int(exp_calls["openInterest"].sum() + exp_puts["openInterest"].sum())
            if total_oi == 0:
                continue

            # Threshold: 95th percentile of OI across this expiration
            all_oi = pd.concat([exp_calls["openInterest"], exp_puts["openInterest"]])
            threshold = float(np.percentile(all_oi, 95)) if len(all_oi) else 0.0

            for option_type, df in [("call", exp_calls), ("put", exp_puts)]:
                for _, row in df.iterrows():
                    oi = row["openInterest"]
                    if oi >= threshold and oi >= self.SMART_MONEY_MIN_ABS_OI:
                        signals.append({
                            "type":             option_type,
                            "expiration":       exp,
                            "dte":              dte,
                            "strike":           float(row["strike"]),
                            "open_interest":    int(oi),
                            "implied_volatility": round(float(row["impliedVolatility"]), 4),
                        })

        assessment      = "INSUFFICIENT_DATA"
        dominant_strike = None

        if signals:
            sm_call_oi = sum(s["open_interest"] for s in signals if s["type"] == "call")
            sm_put_oi  = sum(s["open_interest"] for s in signals if s["type"] == "put")
            if   sm_call_oi > sm_put_oi * 1.5:  assessment = "ACCUMULATING"
            elif sm_put_oi  > sm_call_oi * 1.5:  assessment = "HEDGING"
            else:                                 assessment = "MIXED"
            dominant_strike = max(signals, key=lambda x: x["open_interest"])["strike"]

        return {
            "signals":                 signals[:10],
            "assessment":              assessment,
            "dominant_long_dated_strike": dominant_strike,
        }

    def _detect_unusual_activity(
        self,
        calls_df: pd.DataFrame,
        puts_df:  pd.DataFrame,
        per_exp_results: list,
    ) -> list:
        """
        OI mode:     volume >= UNUSUAL_VOLUME_OI_RATIO × openInterest AND volume >= UNUSUAL_MIN_VOLUME
        Volume mode: volume >= 90th-percentile of all volumes for that expiration AND volume >= UNUSUAL_MIN_VOLUME
        """
        exp_metric = {r["expiration"]: r["metric_used"] for r in per_exp_results}
        unusual    = []

        for option_type, df in [("call", calls_df), ("put", puts_df)]:
            for exp, grp in df.groupby("expiration"):
                metric = exp_metric.get(exp, "volume")

                if metric == "oi":
                    for _, row in grp.iterrows():
                        oi  = row["openInterest"]
                        vol = row["volume"]
                        if vol >= self.UNUSUAL_MIN_VOLUME and oi > 0 and vol >= self.UNUSUAL_VOLUME_OI_RATIO * oi:
                            unusual.append({
                                "type":           option_type,
                                "strike":         float(row["strike"]),
                                "expiration":     exp,
                                "volume":         int(vol),
                                "open_interest":  int(oi),
                                "signal":         "vol_oi_ratio",
                                "ratio":          round(vol / oi, 2),
                            })
                else:
                    # Volume mode: flag strikes in top 10th percentile of volume for this expiration
                    vols = grp["volume"]
                    if vols.empty or vols.sum() == 0:
                        continue
                    threshold = float(np.percentile(vols[vols > 0], self.UNUSUAL_VOL_PERCENTILE)) if (vols > 0).any() else 0
                    for _, row in grp.iterrows():
                        vol = row["volume"]
                        if vol >= threshold and vol >= self.UNUSUAL_MIN_VOLUME:
                            unusual.append({
                                "type":          option_type,
                                "strike":        float(row["strike"]),
                                "expiration":    exp,
                                "volume":        int(vol),
                                "open_interest": int(row["openInterest"]),
                                "signal":        "volume_spike",
                                "ratio":         round(vol / threshold, 2) if threshold > 0 else None,
                            })

        unusual.sort(key=lambda x: x["volume"], reverse=True)
        return unusual[:15]
