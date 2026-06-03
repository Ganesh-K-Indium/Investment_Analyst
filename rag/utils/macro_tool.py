from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json

from app.utils.macro_utils import get_macro_comparison, get_all_macro_latest

class MacroDataInput(BaseModel):
    indicator: str = Field(
        ..., 
        description="The macroeconomic indicator to analyze. Must be one of: 'GDP', 'CPI', 'PCE', 'PPI', 'ECI', or 'ALL'."
    )
    period1: Optional[str] = Field(
        None, 
        description="The target period, e.g., 'Q2 2025', '2025Q2', 'January 2025', or '2025-01'. If empty, defaults to the latest available period."
    )
    period2: Optional[str] = Field(
        None, 
        description="The comparison period, e.g., 'Q2 2024' or 'January 2024'. If empty, defaults to the same period of the previous year (YoY)."
    )
    granularity: Optional[str] = Field(
        None,
        description="The granularity of data: 'monthly' or 'quarterly'. If not specified, it is auto-detected from the periods."
    )
    comparison_type: str = Field(
        "YoY", 
        description="The type of comparison to perform if period2 is empty. Either 'YoY' (Year-over-Year) or 'QoQ' (Quarter-over-Quarter / Month-over-Month). Defaults to 'YoY'."
    )

@tool("macro_data_tool", args_schema=MacroDataInput)
def macro_data_tool(indicator: str, period1: Optional[str] = None, period2: Optional[str] = None, comparison_type: str = "YoY", granularity: Optional[str] = None) -> str:
    """
    Use this tool to fetch and calculate accurate percentage changes for key macroeconomic indicators.
    It automatically handles monthly-to-quarterly aggregation and missing data.
    Supported indicators: GDP, CPI, PCE, PPI, ECI, or ALL.
    """
    if indicator.upper() == "ALL":
        result = get_all_macro_latest(comparison_type)
        return json.dumps(result, indent=2)
    else:
        result = get_macro_comparison(
            indicator=indicator, 
            period1=period1, 
            period2=period2, 
            comparison_type=comparison_type,
            granularity=granularity
        )
        return json.dumps(result, indent=2)
