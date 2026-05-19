"""
Options OI visualization — Plotly grouped bar chart uploaded to Cloudinary.
Mirrors the save_figure_as_base64 / upload_chart_to_cloudinary pattern used
in quant/Stock_Analysis/server_mcp.py.
"""
import asyncio
import traceback
from datetime import datetime

import plotly.graph_objs as go

from cloud_storage import upload_chart_to_cloudinary


async def build_oi_chart(chain_data: dict) -> dict:
    """
    Build a grouped-bar OI distribution chart and upload to Cloudinary.

    Args:
        chain_data: dict returned by OptionsAnalytics.get_chain_for_chart()

    Returns:
        dict with 'chart_url' (Cloudinary URL or local fallback) and metadata.
    """
    try:
        ticker = chain_data.get("ticker", "UNKNOWN")
        expiration = chain_data.get("expiration", "N/A")
        current_price = chain_data.get("current_price")
        max_pain = chain_data.get("max_pain")
        strikes = chain_data.get("strikes", [])
        call_oi = chain_data.get("call_oi", [])
        put_oi = chain_data.get("put_oi", [])
        pc_ratio = chain_data.get("put_call_ratio")

        if not strikes:
            return {"error": "No strike data available for chart."}

        pc_label = f"P/C Ratio: {pc_ratio:.2f}" if pc_ratio else ""
        title = f"{ticker} — Open Interest Distribution | {expiration} | {pc_label}"

        fig = go.Figure()

        fig.add_trace(
            go.Bar(
                x=strikes,
                y=call_oi,
                name="Call OI",
                marker_color="rgba(0, 200, 100, 0.75)",
                hovertemplate="Strike: %{x}<br>Call OI: %{y:,}<extra></extra>",
            )
        )

        fig.add_trace(
            go.Bar(
                x=strikes,
                y=put_oi,
                name="Put OI",
                marker_color="rgba(220, 50, 50, 0.75)",
                hovertemplate="Strike: %{x}<br>Put OI: %{y:,}<extra></extra>",
            )
        )

        # Current price vertical line
        if current_price is not None:
            fig.add_vline(
                x=current_price,
                line_color="yellow",
                line_width=2,
                line_dash="solid",
                annotation_text=f"Price: ${current_price:.2f}",
                annotation_position="top right",
                annotation_font_color="yellow",
            )

        # Max pain vertical line
        if max_pain is not None:
            fig.add_vline(
                x=max_pain,
                line_color="orange",
                line_width=2,
                line_dash="dash",
                annotation_text=f"Max Pain: ${max_pain:.2f}",
                annotation_position="top left",
                annotation_font_color="orange",
            )

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            xaxis=dict(title="Strike Price ($)", tickformat=".0f"),
            yaxis=dict(title="Open Interest", tickformat=","),
            barmode="group",
            template="plotly_dark",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=60, r=40, t=80, b=60),
            height=550,
            width=1100,
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_oi_chart_{expiration}_{timestamp}.png"

        result = await _save_figure(fig, filename)
        result.update(
            {
                "ticker": ticker,
                "expiration": expiration,
                "current_price": current_price,
                "max_pain": max_pain,
                "put_call_ratio": pc_ratio,
                "strikes_shown": len(strikes),
            }
        )
        return result

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


async def _save_figure(fig: go.Figure, filename: str) -> dict:
    """Upload figure to Cloudinary, fall back to local /tmp storage."""
    try:
        result = await upload_chart_to_cloudinary(fig, filename, width=1100, height=550)
        if result.get("success"):
            return {
                "chart_url": result["cloud_url"],
                "chart_generated": True,
                "storage": "cloudinary",
            }
        # Cloudinary failed — local fallback
        print(f"Cloudinary upload failed: {result.get('error')}. Using local fallback.")
    except Exception as e:
        print(f"Cloudinary error: {e}")

    # Local fallback
    try:
        local_path = f"/tmp/{filename}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: fig.write_image(local_path, width=1100, height=550)
        )
        return {
            "chart_url": local_path,
            "chart_generated": True,
            "storage": "local",
        }
    except Exception as e:
        return {
            "chart_url": None,
            "chart_generated": False,
            "error": str(e),
        }
