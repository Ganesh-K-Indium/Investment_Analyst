"""
Options chain visualization — two-panel Plotly chart uploaded to Cloudinary.

Panel 1 (top): Volume / OI distribution by strike (calls green, puts red)
               with current price line, max pain line, and top-strike annotations.
Panel 2 (bottom): Notional dollar flow by strike ($M) — shows where REAL money sits.
"""
import asyncio
import logging
import traceback
from datetime import datetime

import plotly.graph_objs as go
from plotly.subplots import make_subplots

from cloud_storage import upload_chart_to_cloudinary

logger = logging.getLogger("quant.options_mcp.visualization")


async def build_oi_chart(chain_data: dict) -> dict:
    try:
        ticker        = chain_data.get("ticker", "UNKNOWN")
        expiration    = chain_data.get("expiration", "N/A")
        dte           = chain_data.get("dte")
        current_price = chain_data.get("current_price")
        max_pain      = chain_data.get("max_pain")
        strikes       = chain_data.get("strikes", [])
        call_activity = chain_data.get("call_activity", [])
        put_activity  = chain_data.get("put_activity", [])
        call_notional = chain_data.get("call_notional", [])
        put_notional  = chain_data.get("put_notional", [])
        pc_ratio      = chain_data.get("put_call_ratio")
        metric_used   = chain_data.get("metric_used", "volume")
        top_calls     = chain_data.get("top_call_strikes", [])
        top_puts      = chain_data.get("top_put_strikes", [])

        if not strikes:
            return {"error": "No strike data available for chart."}

        # ── Labels ──────────────────────────────────────────────────────────────
        if metric_used == "oi":
            call_label   = "Call Open Interest"
            put_label    = "Put Open Interest"
            y1_label     = "Open Interest"
            mode_tag     = "OI Distribution"
        else:
            call_label   = "Call Volume"
            put_label    = "Put Volume"
            y1_label     = "Volume (Contracts)"
            mode_tag     = "Volume Distribution"

        dte_str   = f"{dte}d to expiry" if dte is not None else ""
        pc_str    = f"P/C: {pc_ratio:.2f}" if pc_ratio is not None else ""
        price_str = f"${current_price:.2f}" if current_price else ""

        main_title = (
            f"<b>{ticker} — Options Activity</b>  |  Expiry: {expiration}"
            + (f"  ({dte_str})" if dte_str else "")
        )
        subtitle = "  |  ".join(filter(None, [price_str and f"Price: {price_str}", pc_str]))

        # ── Figure with 2 stacked subplots ───────────────────────────────────────
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.58, 0.42],
            vertical_spacing=0.08,
            subplot_titles=(
                f"<b>{mode_tag}</b> — Contracts by Strike",
                "<b>Notional Dollar Flow ($M)</b> — Real Capital by Strike",
            ),
        )

        # ── Panel 1: Activity bars ───────────────────────────────────────────────
        fig.add_trace(go.Bar(
            x=strikes, y=call_activity, name=call_label,
            marker_color="rgba(0, 210, 110, 0.80)",
            hovertemplate="Strike: $%{x}<br>" + call_label + ": %{y:,}<extra></extra>",
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=strikes, y=put_activity, name=put_label,
            marker_color="rgba(230, 50, 50, 0.80)",
            hovertemplate="Strike: $%{x}<br>" + put_label + ": %{y:,}<extra></extra>",
        ), row=1, col=1)

        # ── Panel 2: Notional bars ───────────────────────────────────────────────
        fig.add_trace(go.Bar(
            x=strikes, y=call_notional, name="Call Notional ($M)",
            marker_color="rgba(0, 210, 110, 0.60)",
            showlegend=False,
            hovertemplate="Strike: $%{x}<br>Call Flow: $%{y:.2f}M<extra></extra>",
        ), row=2, col=1)

        fig.add_trace(go.Bar(
            x=strikes, y=put_notional, name="Put Notional ($M)",
            marker_color="rgba(230, 50, 50, 0.60)",
            showlegend=False,
            hovertemplate="Strike: $%{x}<br>Put Flow: $%{y:.2f}M<extra></extra>",
        ), row=2, col=1)

        # ── Current price line (both panels) ────────────────────────────────────
        if current_price is not None:
            for row in [1, 2]:
                fig.add_vline(
                    x=current_price, row=row, col=1,
                    line_color="#FFD700", line_width=2, line_dash="solid",
                )
            # Annotation only on top panel
            fig.add_annotation(
                x=current_price, y=1.02, xref="x", yref="paper",
                text=f"<b>Price ${current_price:.2f}</b>",
                showarrow=False, font=dict(color="#FFD700", size=11),
                bgcolor="rgba(0,0,0,0.6)", bordercolor="#FFD700", borderwidth=1,
            )

        # ── Max pain line (OI mode only, top panel) ──────────────────────────────
        if max_pain is not None:
            fig.add_vline(
                x=max_pain, row=1, col=1,
                line_color="#FF8C00", line_width=2, line_dash="dash",
            )
            fig.add_annotation(
                x=max_pain, y=0.58, xref="x", yref="paper",
                text=f"<b>Max Pain ${max_pain:.2f}</b>",
                showarrow=False, font=dict(color="#FF8C00", size=10),
                bgcolor="rgba(0,0,0,0.6)", bordercolor="#FF8C00", borderwidth=1,
            )

        # ── Top call strike annotations (panel 1) ───────────────────────────────
        if top_calls and call_activity:
            max_act = max(call_activity + put_activity) if call_activity else 1
            for rank, tc in enumerate(top_calls[:3]):
                s = tc["strike"]
                v = tc["call_activity"]
                fig.add_annotation(
                    x=s, y=v + max_act * 0.04,
                    xref="x", yref="y",
                    text=f"<b>${s:.0f}C</b>",
                    showarrow=True, arrowhead=2, arrowcolor="rgba(0,210,110,0.7)",
                    font=dict(color="rgba(0,210,110,1)", size=9),
                    ax=0, ay=-25 - rank * 12,
                )

        # ── Top put strike annotations (panel 1) ────────────────────────────────
        if top_puts and put_activity:
            max_act = max(call_activity + put_activity) if put_activity else 1
            for rank, tp in enumerate(top_puts[:3]):
                s = tp["strike"]
                v = tp["put_activity"]
                fig.add_annotation(
                    x=s, y=v + max_act * 0.04,
                    xref="x", yref="y",
                    text=f"<b>${s:.0f}P</b>",
                    showarrow=True, arrowhead=2, arrowcolor="rgba(230,50,50,0.7)",
                    font=dict(color="rgba(230,50,50,1)", size=9),
                    ax=0, ay=-25 - rank * 12,
                )

        # ── Layout ───────────────────────────────────────────────────────────────
        fig.update_layout(
            title=dict(
                text=f"{main_title}<br><sup style='color:#aaa'>{subtitle}</sup>",
                font=dict(size=15, color="white"),
                x=0.01,
            ),
            template="plotly_dark",
            barmode="group",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.04,
                xanchor="right", x=1, font=dict(size=11),
            ),
            margin=dict(l=65, r=40, t=110, b=55),
            height=720,
            width=1200,
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
        )

        fig.update_xaxes(
            title_text="Strike Price ($)", row=2, col=1,
            tickformat=".0f", gridcolor="rgba(255,255,255,0.07)",
        )
        fig.update_xaxes(gridcolor="rgba(255,255,255,0.07)", row=1, col=1)
        fig.update_yaxes(title_text=y1_label, row=1, col=1,
                         tickformat=",", gridcolor="rgba(255,255,255,0.07)")
        fig.update_yaxes(title_text="Flow ($M)", row=2, col=1,
                         tickformat=".1f", gridcolor="rgba(255,255,255,0.07)")

        # Subtitle on subplot titles
        for ann in fig.layout.annotations:
            ann.update(font=dict(size=11, color="#cccccc"))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"{ticker}_options_chart_{expiration}_{timestamp}.png"

        result = await _save_figure(fig, filename)
        result.update({
            "ticker":         ticker,
            "expiration":     expiration,
            "dte":            dte,
            "metric_used":    metric_used,
            "current_price":  current_price,
            "max_pain":       max_pain,
            "put_call_ratio": float(pc_ratio) if pc_ratio is not None else None,
            "strikes_shown":  len(strikes),
        })
        return result

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


async def _save_figure(fig: go.Figure, filename: str) -> dict:
    try:
        result = await upload_chart_to_cloudinary(fig, filename, width=1200, height=720)
        if result.get("success"):
            return {"chart_url": result["cloud_url"], "chart_generated": True, "storage": "cloudinary"}
        logger.warning("Cloudinary upload failed: %s. Using local fallback.", result.get('error'))
    except Exception as e:
        logger.error("Cloudinary error: %s", e)

    try:
        local_path = f"/tmp/{filename}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: fig.write_image(local_path, width=1200, height=720))
        return {"chart_url": local_path, "chart_generated": True, "storage": "local"}
    except Exception as e:
        return {"chart_url": None, "chart_generated": False, "error": str(e)}
