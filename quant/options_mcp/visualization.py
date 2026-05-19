"""
Options chain visualization — Plotly grouped bar chart uploaded to Cloudinary.
Adapts title, axis label, and annotations based on metric_used (oi | volume).
Mirrors the upload_chart_to_cloudinary pattern from quant/Stock_Analysis/server_mcp.py.
"""
import asyncio
import traceback
from datetime import datetime

import plotly.graph_objs as go

from cloud_storage import upload_chart_to_cloudinary


async def build_oi_chart(chain_data: dict) -> dict:
    """
    Build a grouped-bar activity distribution chart and upload to Cloudinary.

    Args:
        chain_data: dict returned by OptionsAnalytics.get_chain_for_chart()

    Returns:
        dict with 'chart_url' (Cloudinary URL or local fallback) and metadata.
    """
    try:
        ticker        = chain_data.get("ticker", "UNKNOWN")
        expiration    = chain_data.get("expiration", "N/A")
        current_price = chain_data.get("current_price")
        max_pain      = chain_data.get("max_pain")         # None in volume mode
        strikes       = chain_data.get("strikes", [])
        call_activity = chain_data.get("call_activity", [])
        put_activity  = chain_data.get("put_activity", [])
        pc_ratio      = chain_data.get("put_call_ratio")
        metric_used   = chain_data.get("metric_used", "volume")

        if not strikes:
            return {"error": "No strike data available for chart."}

        # Labels adapt to metric
        if metric_used == "oi":
            call_label  = "Call OI"
            put_label   = "Put OI"
            y_axis_label = "Open Interest"
            chart_type   = "OI Distribution"
        else:
            call_label   = "Call Volume"
            put_label    = "Put Volume"
            y_axis_label = "Volume (Today)"
            chart_type   = "Volume Distribution"

        pc_label = f"P/C Ratio: {pc_ratio:.2f}" if pc_ratio is not None else ""
        title    = f"{ticker} — {chart_type} | {expiration} | {pc_label}"

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=strikes,
            y=call_activity,
            name=call_label,
            marker_color="rgba(0, 200, 100, 0.75)",
            hovertemplate=f"Strike: %{{x}}<br>{call_label}: %{{y:,}}<extra></extra>",
        ))

        fig.add_trace(go.Bar(
            x=strikes,
            y=put_activity,
            name=put_label,
            marker_color="rgba(220, 50, 50, 0.75)",
            hovertemplate=f"Strike: %{{x}}<br>{put_label}: %{{y:,}}<extra></extra>",
        ))

        # Current price line
        if current_price is not None:
            fig.add_vline(
                x=current_price,
                line_color="yellow", line_width=2, line_dash="solid",
                annotation_text=f"Price: ${current_price:.2f}",
                annotation_position="top right",
                annotation_font_color="yellow",
            )

        # Max pain line — only in OI mode
        if max_pain is not None:
            fig.add_vline(
                x=max_pain,
                line_color="orange", line_width=2, line_dash="dash",
                annotation_text=f"Max Pain: ${max_pain:.2f}",
                annotation_position="top left",
                annotation_font_color="orange",
            )

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            xaxis=dict(title="Strike Price ($)", tickformat=".0f"),
            yaxis=dict(title=y_axis_label, tickformat=","),
            barmode="group",
            template="plotly_dark",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=60, r=40, t=80, b=60),
            height=550,
            width=1100,
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"{ticker}_{metric_used}_chart_{expiration}_{timestamp}.png"

        result = await _save_figure(fig, filename)
        result.update({
            "ticker":         ticker,
            "expiration":     expiration,
            "metric_used":    metric_used,
            "current_price":  current_price,
            "max_pain":       max_pain,
            "put_call_ratio": pc_ratio,
            "strikes_shown":  len(strikes),
        })
        return result

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


async def _save_figure(fig: go.Figure, filename: str) -> dict:
    try:
        result = await upload_chart_to_cloudinary(fig, filename, width=1100, height=550)
        if result.get("success"):
            return {"chart_url": result["cloud_url"], "chart_generated": True, "storage": "cloudinary"}
        print(f"Cloudinary upload failed: {result.get('error')}. Using local fallback.")
    except Exception as e:
        print(f"Cloudinary error: {e}")

    try:
        local_path = f"/tmp/{filename}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: fig.write_image(local_path, width=1100, height=550))
        return {"chart_url": local_path, "chart_generated": True, "storage": "local"}
    except Exception as e:
        return {"chart_url": None, "chart_generated": False, "error": str(e)}
