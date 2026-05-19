"""
Options chain analytics engine — pure deterministic Python, no LLM calls.
All pattern detection, OI clustering, and signal generation is rule-based.
"""
import traceback
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


class OptionsAnalytics:
    """
    Deterministic analytics engine for options chain data.
    Produces structured JSON consumed by the LLM for natural-language narration.
    """

    UNUSUAL_VOLUME_OI_RATIO = 3.0
    UNUSUAL_MIN_VOLUME = 500
    SMART_MONEY_DTE_THRESHOLD = 90
    SMART_MONEY_OI_PERCENTILE = 95
    SMART_MONEY_MIN_ABS_OI = 1000
    BULLISH_PC_THRESHOLD = 0.7
    BEARISH_PC_THRESHOLD = 1.3
    TOP_N_STRIKES = 5
    # Price window for chart / strike filtering (±15% of current price)
    PRICE_WINDOW_PCT = 0.15
    # Expirations to pull: nearest N near-term + M long-dated
    MAX_NEAR_TERM = 4
    MAX_LONG_DATED = 1

    def analyze(self, ticker_symbol: str, expiration_date: Optional[str] = None) -> dict:
        """
        Run the full options analytics pipeline for a ticker.

        Returns a structured dict ready for LLM narration — never exposes raw
        option chain DataFrames.
        """
        errors = []
        ticker_symbol = ticker_symbol.upper().strip()

        try:
            tk = yf.Ticker(ticker_symbol)
        except Exception as e:
            return {"error": f"Cannot create ticker object for {ticker_symbol}: {e}"}

        # ── Current price ────────────────────────────────────────────────────
        current_price = self._get_current_price(tk, errors)

        # ── Available expirations ────────────────────────────────────────────
        try:
            all_expirations = list(tk.options)
        except Exception as e:
            return {"error": f"No options data available for {ticker_symbol}: {e}"}

        if not all_expirations:
            return {"error": f"No options expirations found for {ticker_symbol}"}

        today = datetime.now(timezone.utc).date()

        # ── Select expirations to analyze ────────────────────────────────────
        if expiration_date:
            if expiration_date not in all_expirations:
                return {
                    "error": f"Expiration '{expiration_date}' not available.",
                    "available_expirations": all_expirations[:8],
                }
            expirations_to_analyze = [expiration_date]
        else:
            expirations_to_analyze = self._select_expirations(all_expirations, today)

        # ── Fetch chains ─────────────────────────────────────────────────────
        all_calls, all_puts, per_exp_results, near_term_oi_pool = (
            self._fetch_chains(tk, expirations_to_analyze, today, errors)
        )

        if not all_calls:
            return {
                "error": "Failed to fetch any option chain data.",
                "details": errors,
            }

        calls_df = pd.concat(all_calls, ignore_index=True)
        puts_df = pd.concat(all_puts, ignore_index=True)

        # ── Aggregate metrics ────────────────────────────────────────────────
        total_call_oi = int(calls_df["openInterest"].sum())
        total_put_oi = int(puts_df["openInterest"].sum())
        agg_pc_ratio = (
            round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else None
        )
        sentiment = self._classify_sentiment(agg_pc_ratio)

        # ── OI concentration zones ───────────────────────────────────────────
        bullish_zones = self._top_oi_strikes(calls_df, "call", total_call_oi)
        bearish_zones = self._top_oi_strikes(puts_df, "put", total_put_oi)

        # ── Support / resistance from OI ─────────────────────────────────────
        support_levels, resistance_levels = self._derive_levels(
            calls_df, puts_df, current_price
        )

        # ── Max pain per expiration ──────────────────────────────────────────
        max_pain_list = [
            {"expiration": r["expiration"], "strike": r["max_pain_strike"]}
            for r in per_exp_results
            if r["max_pain_strike"] is not None
        ]

        # ── Smart money (long-dated OI) ───────────────────────────────────────
        smart_money = self._scan_smart_money(
            calls_df, puts_df, expirations_to_analyze, today, near_term_oi_pool
        )

        # ── Unusual activity ─────────────────────────────────────────────────
        unusual = self._detect_unusual_activity(calls_df, puts_df)

        return {
            "ticker": ticker_symbol,
            "current_price": (
                round(float(current_price), 2) if current_price is not None else None
            ),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "expirations_analyzed": expirations_to_analyze,
            "all_available_expirations": all_expirations,
            "per_expiration": per_exp_results,
            "aggregate": {
                "put_call_ratio": agg_pc_ratio,
                "sentiment": sentiment,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "sentiment_thresholds": {
                    "bullish_below": self.BULLISH_PC_THRESHOLD,
                    "bearish_above": self.BEARISH_PC_THRESHOLD,
                },
            },
            "concentration_zones": {
                "bullish": bullish_zones,
                "bearish": bearish_zones,
            },
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "max_pain": max_pain_list,
            "smart_money": smart_money,
            "unusual_activity": unusual,
            "data_quality": {
                "expirations_requested": len(expirations_to_analyze),
                "expirations_with_data": len(per_exp_results),
                "errors": errors,
            },
        }

    # ── Expiration date helpers ──────────────────────────────────────────────

    def get_expiration_dates_with_dte(self, ticker_symbol: str) -> dict:
        """Return all expiration dates bucketed by DTE (days to expiration)."""
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
                "ticker": ticker_symbol.upper(),
                "total_expirations": len(expirations),
                "near_term_le30": near_term,
                "mid_term_31_90": mid_term,
                "long_dated_gt90": long_dated,
                "all_expirations": list(expirations),
            }
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    def get_chain_for_chart(
        self, ticker_symbol: str, expiration_date: str, current_price: Optional[float] = None
    ) -> dict:
        """
        Return calls/puts OI by strike for the visualization layer.
        Filtered to price ± PRICE_WINDOW_PCT to keep charts readable.
        """
        try:
            tk = yf.Ticker(ticker_symbol.upper().strip())
            chain = tk.option_chain(expiration_date)
            calls = chain.calls.copy()
            puts = chain.puts.copy()

            for df in [calls, puts]:
                df["openInterest"] = (
                    pd.to_numeric(df.get("openInterest", 0), errors="coerce").fillna(0)
                )
                df["strike"] = pd.to_numeric(df.get("strike", 0), errors="coerce").fillna(0)

            if current_price is None:
                info = tk.info
                current_price = (
                    info.get("currentPrice")
                    or info.get("regularMarketPrice")
                    or info.get("ask")
                )

            if current_price:
                lo = current_price * (1 - self.PRICE_WINDOW_PCT)
                hi = current_price * (1 + self.PRICE_WINDOW_PCT)
                calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
                puts = puts[(puts["strike"] >= lo) & (puts["strike"] <= hi)]

            # Merge on strike for chart
            call_oi = calls[["strike", "openInterest"]].rename(
                columns={"openInterest": "call_oi"}
            )
            put_oi = puts[["strike", "openInterest"]].rename(
                columns={"openInterest": "put_oi"}
            )
            merged = pd.merge(call_oi, put_oi, on="strike", how="outer").fillna(0)
            merged = merged.sort_values("strike")

            # Max pain for this expiration
            max_pain = self._calculate_max_pain(calls, puts)

            return {
                "ticker": ticker_symbol.upper(),
                "expiration": expiration_date,
                "current_price": (
                    round(float(current_price), 2) if current_price else None
                ),
                "max_pain": max_pain,
                "strikes": merged["strike"].tolist(),
                "call_oi": merged["call_oi"].astype(int).tolist(),
                "put_oi": merged["put_oi"].astype(int).tolist(),
                "put_call_ratio": (
                    round(float(merged["put_oi"].sum()) / float(merged["call_oi"].sum()), 4)
                    if merged["call_oi"].sum() > 0
                    else None
                ),
            }
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get_current_price(self, tk: yf.Ticker, errors: list) -> Optional[float]:
        try:
            info = tk.info
            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("ask")
            )
            if price is not None:
                return float(price)
            hist = tk.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            errors.append(f"Price fetch error: {e}")
        return None

    def _select_expirations(self, all_expirations: list, today) -> list:
        exp_with_dte = []
        for exp in all_expirations:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte > 0:
                    exp_with_dte.append((exp, dte))
            except Exception:
                pass

        near_term = [e for e, d in exp_with_dte if d <= self.SMART_MONEY_DTE_THRESHOLD][
            : self.MAX_NEAR_TERM
        ]
        long_dated = [e for e, d in exp_with_dte if d > self.SMART_MONEY_DTE_THRESHOLD][
            : self.MAX_LONG_DATED
        ]
        result = near_term + long_dated
        return result if result else [all_expirations[0]]

    def _fetch_chains(
        self, tk: yf.Ticker, expirations: list, today, errors: list
    ):
        all_calls, all_puts, per_exp_results, near_term_oi_pool = [], [], [], []

        for exp in expirations:
            try:
                chain = tk.option_chain(exp)
                calls = chain.calls.copy()
                puts = chain.puts.copy()

                for df in [calls, puts]:
                    df["openInterest"] = (
                        pd.to_numeric(df.get("openInterest", 0), errors="coerce").fillna(0)
                    )
                    df["volume"] = (
                        pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
                    )
                    df["strike"] = (
                        pd.to_numeric(df.get("strike", 0), errors="coerce").fillna(0)
                    )
                    df["impliedVolatility"] = (
                        pd.to_numeric(
                            df.get("impliedVolatility", 0), errors="coerce"
                        ).fillna(0)
                    )

                calls["expiration"] = exp
                puts["expiration"] = exp

                call_oi = int(calls["openInterest"].sum())
                put_oi = int(puts["openInterest"].sum())
                pc = round(put_oi / call_oi, 4) if call_oi > 0 else None
                max_pain_strike = self._calculate_max_pain(calls, puts)

                per_exp_results.append(
                    {
                        "expiration": exp,
                        "call_oi": call_oi,
                        "put_oi": put_oi,
                        "put_call_ratio": pc,
                        "max_pain_strike": max_pain_strike,
                    }
                )

                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                if (exp_date - today).days <= self.SMART_MONEY_DTE_THRESHOLD:
                    near_term_oi_pool.extend(calls["openInterest"].tolist())
                    near_term_oi_pool.extend(puts["openInterest"].tolist())

                all_calls.append(calls)
                all_puts.append(puts)

            except Exception as e:
                errors.append(f"Expiration {exp}: {e}")

        return all_calls, all_puts, per_exp_results, near_term_oi_pool

    def _calculate_max_pain(
        self, calls: pd.DataFrame, puts: pd.DataFrame
    ) -> Optional[float]:
        """Strike that minimises total dollar loss to option buyers at expiry."""
        try:
            all_strikes = sorted(
                set(calls["strike"].unique()) | set(puts["strike"].unique())
            )
            if not all_strikes:
                return None

            min_pain, max_pain_strike = float("inf"), None
            for k in all_strikes:
                call_pain = float(
                    ((calls["strike"] - k).clip(lower=0) * calls["openInterest"]).sum()
                )
                put_pain = float(
                    ((k - puts["strike"]).clip(lower=0) * puts["openInterest"]).sum()
                )
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

    def _top_oi_strikes(
        self, df: pd.DataFrame, option_type: str, total_oi: int
    ) -> list:
        oi_col = "openInterest"
        grouped = (
            df.groupby("strike")[oi_col]
            .sum()
            .sort_values(ascending=False)
            .head(self.TOP_N_STRIKES)
        )
        result = []
        for strike, oi in grouped.items():
            best_row = df[df["strike"] == strike].sort_values(oi_col, ascending=False)
            expiration = best_row.iloc[0]["expiration"] if not best_row.empty else "N/A"
            pct = round(100 * oi / total_oi, 2) if total_oi > 0 else 0
            entry = {
                "strike": float(strike),
                "expiration": expiration,
                f"{option_type}_oi": int(oi),
                "pct_of_total": pct,
            }
            result.append(entry)
        return result

    def _derive_levels(
        self,
        calls_df: pd.DataFrame,
        puts_df: pd.DataFrame,
        current_price: Optional[float],
    ):
        if current_price is None:
            return [], []

        below = puts_df[puts_df["strike"] < current_price]
        above = calls_df[calls_df["strike"] > current_price]

        support = (
            below.groupby("strike")["openInterest"]
            .sum()
            .sort_values(ascending=False)
            .head(3)
            .index.tolist()
        )
        resistance = (
            above.groupby("strike")["openInterest"]
            .sum()
            .sort_values(ascending=False)
            .head(3)
            .index.tolist()
        )
        return (
            sorted([float(s) for s in support], reverse=True),
            sorted([float(s) for s in resistance]),
        )

    def _scan_smart_money(
        self,
        calls_df: pd.DataFrame,
        puts_df: pd.DataFrame,
        expirations: list,
        today,
        near_term_oi_pool: list,
    ) -> dict:
        sm_threshold = (
            float(np.percentile(near_term_oi_pool, self.SMART_MONEY_OI_PERCENTILE))
            if near_term_oi_pool
            else 0.0
        )

        signals = []
        for exp in expirations:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if dte <= self.SMART_MONEY_DTE_THRESHOLD:
                continue

            for option_type, df in [("call", calls_df), ("put", puts_df)]:
                exp_df = df[df["expiration"] == exp]
                for _, row in exp_df.iterrows():
                    oi = row["openInterest"]
                    if oi > sm_threshold and oi >= self.SMART_MONEY_MIN_ABS_OI:
                        signals.append(
                            {
                                "type": option_type,
                                "expiration": exp,
                                "dte": dte,
                                "strike": float(row["strike"]),
                                "open_interest": int(oi),
                                "implied_volatility": round(
                                    float(row["impliedVolatility"]), 4
                                ),
                            }
                        )

        assessment = "INSUFFICIENT_DATA"
        dominant_strike = None

        if signals:
            sm_call_oi = sum(s["open_interest"] for s in signals if s["type"] == "call")
            sm_put_oi = sum(s["open_interest"] for s in signals if s["type"] == "put")
            if sm_call_oi > sm_put_oi * 1.5:
                assessment = "ACCUMULATING"
            elif sm_put_oi > sm_call_oi * 1.5:
                assessment = "HEDGING"
            else:
                assessment = "MIXED"
            dominant_strike = max(signals, key=lambda x: x["open_interest"])["strike"]

        return {
            "signals": signals[:10],
            "assessment": assessment,
            "dominant_long_dated_strike": dominant_strike,
            "oi_threshold_used": round(sm_threshold, 0),
        }

    def _detect_unusual_activity(
        self, calls_df: pd.DataFrame, puts_df: pd.DataFrame
    ) -> list:
        unusual = []
        for option_type, df in [("call", calls_df), ("put", puts_df)]:
            for _, row in df.iterrows():
                oi = row["openInterest"]
                vol = row["volume"]
                if (
                    vol >= self.UNUSUAL_MIN_VOLUME
                    and oi > 0
                    and vol >= self.UNUSUAL_VOLUME_OI_RATIO * oi
                ):
                    unusual.append(
                        {
                            "type": option_type,
                            "strike": float(row["strike"]),
                            "expiration": row["expiration"],
                            "volume": int(vol),
                            "open_interest": int(oi),
                            "vol_oi_ratio": round(vol / oi, 2),
                        }
                    )

        unusual.sort(key=lambda x: x["vol_oi_ratio"], reverse=True)
        return unusual[:10]
