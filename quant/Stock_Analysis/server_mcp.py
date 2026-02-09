import pandas as pd
import yfinance as yf
from fastmcp import FastMCP
from plotly.subplots import make_subplots
import plotly.graph_objs as go
import plotly.io as pio
import numpy as np
import ta # Technical Analysis library
from ta.trend import SMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from scipy.signal import argrelextrema
import asyncio
import traceback
import base64
import sys
import os
import base64
import google.generativeai as genai
import datetime
from cloud_storage import upload_chart_to_cloudinary
from dotenv import load_dotenv
load_dotenv()
# Initialize FastMCP server
technicalanalysis_server = FastMCP(
    "technicalanalysis",
    instructions="""
    # Technical Trading Analysis MCP Server

    This server generates comprehensive technical analysis charts for stocks using historical data from Yahoo Finance.

    ## Single Stock Tools:
        - `get_stock_sma`: Calculate the Simple Moving Average (SMA) for a given ticker symbol, start date, and end date.
        - `get_stock_rsi`: Generate an RSI (Relative Strength Index) chart for a stock between given dates.
        - `get_stock_bollingerbands`: Calculate the Bollinger Bands (BB) for a given ticker symbol, start date, and end date.
        - `get_stock_macd`: Calculate the Moving Average Convergence Divergence (MACD) for a given ticker symbol, start date, and end date.
        - `get_stock_volume`: Calculate the Volume for a given ticker symbol, start date, and end date.
        - `get_stock_support_resistance`: Calculate the Support and Resistance levels for a given ticker symbol, start date, and end date.
        - `get_all_technical_analysis`: Generate comprehensive technical analysis with all indicators for a single stock.
        - `get_chart_summary`: Generate AI-powered summary of a chart image.
    
    ## Multi-Stock Comparison Tools (NEW):
        - `get_multi_stock_sma`: Compare Simple Moving Averages for multiple stocks on the same chart.
        - `get_multi_stock_rsi`: Compare RSI for multiple stocks on the same chart.
        - `get_multi_stock_bollingerbands`: Compare Bollinger Bands for multiple stocks on the same chart.
        - `get_multi_stock_macd`: Compare MACD for multiple stocks on the same chart.
        - `get_multi_stock_volume`: Compare Volume trends for multiple stocks (separate subplots).
    
    Use multi-stock tools when comparing 2 or more companies/tickers.
    """
    )

async def fetch_stock_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Asynchronously downloads stock data for a given ticker and date range.
    Returns a DataFrame or raises an exception if download fails or is empty.
    """
    try:
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, lambda: yf.download(ticker, start=start_date, end=end_date))

        if df.empty:
            raise ValueError(f"No data found for {ticker} between {start_date} and {end_date}.")
    
        if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

        return df

    except Exception as e:
        raise RuntimeError(f"Failed to fetch data for {ticker}: {e}\n\nTraceback:\n{traceback.format_exc()}")
                
            
async def save_figure_as_base64(fig: go.Figure, filename: str, width: int = 1000, height: int = 600, use_cloudinary: bool = True) -> dict:
    """
    Save figure to Cloudinary and return the cloud URL.
    Falls back to local storage if Cloudinary upload fails.
    
    Args:
        fig: Plotly figure object
        filename: Chart filename
        width: Chart width
        height: Chart height
        use_cloudinary: Whether to upload to Cloudinary (default True)
    
    Returns:
        Dict with 'filename' (URL if cloud upload successful, or local path) and 'success' status
    """
    try:
        if use_cloudinary:
            result = await upload_chart_to_cloudinary(fig, filename, width, height)
            
            if result.get("success"):
                return {
                    "filename": result["cloud_url"],
                    "success": True,
                    "source": "cloudinary"
                }
            else:
                print(f"Cloudinary upload failed: {result.get('error')}. Falling back to local storage.")
        
        # Fallback to local storage
        loop = asyncio.get_event_loop()    
        await loop.run_in_executor(None, lambda: fig.write_image(filename, width=width, height=height))
        full_path = os.path.abspath(filename)
        
        return {
            "filename": full_path,
            "success": True,
            "source": "local"
        }
    except Exception as e:
        return {
            "error": str(e),
            "traceback": traceback.format_exc()
        }

            

# Indicators Simple Moving Average
@technicalanalysis_server.tool(
        name="get_stock_sma",
        description="""Calculate the Simple Moving Average (SMA) for the user's given ticker in the given date range."
        Args:
                ticker:str 
                        the ticker symbol given by the user for getting Simple Moving Average (SMA) of he company.
                start_date:str
                        the start date given by the user for getting Simple Moving Average (SMA) of the company.
                end_date:str
                        the end date given by the user for getting Simple Moving Average (SMA) of the company.
        Returns:
                str
                        A message indicating whether the SMA was successfully calculated or not.     
        """
)
async def get_stock_sma(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the Simple Moving Average (SMA) for a given ticker symbol, start date, and end date.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate SMA for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate SMA for a stock (format: 'YYYY-MM-DD')
        """
        try:
                df = await fetch_stock_data(ticker, start_date, end_date)
                

                close = df['Close']
                df['SMA_20'] = SMAIndicator(close, window=20).sma_indicator()
                df['SMA_100'] = SMAIndicator(close, window=100).sma_indicator()
                df['SMA_200'] = SMAIndicator(close, window=200).sma_indicator()
                df['SMA_300'] = SMAIndicator(close, window=300).sma_indicator()

                
        
                fig = go.Figure()
                df.index = df.index.strftime('%Y-%m-%d')
                fig.add_trace(go.Scatter(x=df.index, y=close, name='Close', line=dict(color='white')))
                fig.add_trace(go.Scatter(x=df.index, y=df['SMA_20'], name='SMA 20', line=dict(color='orange')))
                fig.add_trace(go.Scatter(x=df.index, y=df['SMA_100'], name='SMA 100', line=dict(color='blue')))
                fig.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], name='SMA 200', line=dict(color='green')))
                fig.add_trace(go.Scatter(x=df.index, y=df['SMA_300'], name='SMA 300', line=dict(color='purple')))
                print(df.index)
                fig.update_layout(
                title=f"{ticker.upper()} - Simple Moving Averages",
                template='plotly_dark',
                xaxis_title='Date',
                yaxis_title='Price',
        
                height=600,
                width=1000
                
        )
        
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{ticker}_sma_chart_{timestamp}.png"
                result = await save_figure_as_base64(fig, filename) 
                if "error" in result:
                        return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}

                return {
                "ticker": ticker,
                "chart_generated": True,
                "image_filename": result["filename"],
                "message": f"SMA chart for {ticker} has been generated and saved to {result['filename']}. The chart shows 20, 100, 200, and 300-day Simple Moving Averages."
                }
                
        except Exception as e:
               return {"ticker": ticker, "error": str(e), "traceback": traceback.format_exc()}
      

@technicalanalysis_server.tool(
    name="get_stock_rsi",
    description="""
    Generate an RSI (Relative Strength Index) chart for a stock between given dates.

    Args:
        ticker: str - Stock symbol (e.g., "NFLX")
        start_date: str - Start date in 'YYYY-MM-DD'
        end_date: str - End date in 'YYYY-MM-DD'

    Output:
        Base64-encoded chart of RSI values with thresholds.
    """
)
async def get_stock_rsi(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the Relative Strength Index (RSI) for a given ticker symbol, start date, and end date.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate RSI for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate RSI for a stock (format: 'YYYY-MM-DD')
        """
        
        df = await fetch_stock_data(ticker, start_date, end_date)
        close=df['Close']
        rsi = RSIIndicator(close=close, window=14)
        
        df['RSI'] = rsi.rsi()

        # Plotting
        fig = go.Figure()
        df.index = df.index.strftime('%Y-%m-%d')
        fig.add_trace(go.Scatter(
            x=df.index,
            y=df['RSI'],
            name='RSI (14)',
            line=dict(color='lime')
        ))
        
        # Overbought / Oversold lines
        fig.add_shape(type="line", x0=df.index[0], x1=df.index[-1], y0=70, y1=70,
                      line=dict(color="red", dash="dash"), name='Overbought')
        fig.add_shape(type="line", x0=df.index[0], x1=df.index[-1], y0=30, y1=30,
                      line=dict(color="blue", dash="dash"), name='Oversold')

        fig.update_layout(
            title=f"{ticker.upper()} - RSI (Relative Strength Index)",
            template='plotly_dark',
            xaxis_title='Date',
            yaxis_title='RSI Value',
            yaxis=dict(range=[0, 100]),
            height=500,
            width=1000,
            
        )

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_rsi_chart_{timestamp}.png"
        result= await save_figure_as_base64(fig, filename)
        if "error" in result:
                return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}
        else:
                # Get current RSI value for the response
                current_rsi = df['RSI'].iloc[-1] if not df['RSI'].empty else None
                rsi_status = "overbought (>70)" if current_rsi and current_rsi > 70 else "oversold (<30)" if current_rsi and current_rsi < 30 else "neutral"
                return {
            "ticker": ticker,
            "chart_generated": True,
            "filename": result["filename"],
            "current_rsi": round(current_rsi, 2) if current_rsi else None,
            "rsi_status": rsi_status,
            "message": f"RSI chart for {ticker} has been generated and saved to {result['filename']}. Current RSI: {round(current_rsi, 2) if current_rsi else 'N/A'} ({rsi_status})."
        }

#Bollinger Bands – show volatility using upper and lower band
@technicalanalysis_server.tool(
        name="get_stock_bollingerbands",
        description="""Calculate the Bollinger Bands (BB) for the user's given ticker in the given date range."
        Args:
                ticker:str 
                        the ticker symbol given by the user for getting Bollinger Bands (BB) of he company.
                start_date:str
                        the start date given by the user for getting Bollinger Bands (BB) of the company.
                end_date:str
                        the end date given by the user for getting Bollinger Bands (BB) of the company.
        Returns:
                str
                        A message indicating whether the Bollinger Bands (BB) was successfully calculated or not.     
        """
)
async def get_stock_bollingerbands(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the Bollingerband (bb) for a given ticker symbol, start date, and end date.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate BollingerBand (bb) for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate BollingerBand (bb) for a stock (format: 'YYYY-MM-DD')
        """
        df = await fetch_stock_data(ticker, start_date, end_date)
        close = df['Close']
        bb = BollingerBands(close=close, window=20, window_dev=2)
        
        df['Upper_BB'] = bb.bollinger_hband()
        df['Lower_BB'] = bb.bollinger_lband()
        df['SMA_20'] = bb.bollinger_mavg()

        # Bollinger Bands
        fig = go.Figure()
        df.index = df.index.strftime('%Y-%m-%d')
        fig.add_trace(go.Scatter(
            x=df.index, y=df['Upper_BB'],
            name='Upper Band',
            line=dict(color='lightblue', dash='dot')
        ))

        fig.add_trace(go.Scatter(
            x=df.index, y=df['Lower_BB'],
            name='Lower Band',
            line=dict(color='lightblue', dash='dot')
        ))

        fig.add_trace(go.Scatter(
            x=df.index, y=df['SMA_20'],
            name='SMA 20',
            line=dict(color='orange', width=1)
        ))

        fig.update_layout(
            title=f"{ticker.upper()} - Bollinger Bands",
            template='plotly_dark',
            xaxis_title='Date',
            yaxis_title='Price',
            height=600,
            width=1000
        )

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_bollingerbands_chart_{timestamp}.png"
        result= await save_figure_as_base64(fig, filename)
        if "error" in result:
                return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}
        else:
                # Get latest values for the response
                upper_bb = df['Upper_BB'].iloc[-1] if not df['Upper_BB'].empty else None
                lower_bb = df['Lower_BB'].iloc[-1] if not df['Lower_BB'].empty else None
                sma_20 = df['SMA_20'].iloc[-1] if not df['SMA_20'].empty else None
                return {
                "ticker": ticker,
                "chart_generated": True,
                "image_filename": result["filename"],
                "upper_band": round(upper_bb, 2) if upper_bb else None,
                "lower_band": round(lower_bb, 2) if lower_bb else None,
                "middle_band_sma20": round(sma_20, 2) if sma_20 else None,
                "message": f"Bollinger Bands chart for {ticker} has been generated and saved to {result['filename']}. Upper Band: {round(upper_bb, 2) if upper_bb else 'N/A'}, Lower Band: {round(lower_bb, 2) if lower_bb else 'N/A'}."
                }
                
#MACD (Moving Average Convergence Divergence) – shows trend changes.
@technicalanalysis_server.tool(name="get_stock_macd",
    description="""Calculate the Moving Average Convergence Divergence (MACD) for the user's given ticker in the given date range.
        Args:
                ticker:str
                        the ticker symbol given by the user for getting Moving Average Convergence Divergence (MACD) of he company.
                start_date:str
                        the start date given by the user for getting Moving Average Convergence Divergence (MACD) of the company.
                end_date:str
                        the end date given by the user for getting Moving Average Convergence Divergence (MACD) of the company.
                        Returns:str
                                A message indicating whether the Moving Average Convergence Divergence (MACD) was successfully calculated or not.
        """
)

async def get_stock_macd(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the Moving Average Convergence Divergence (MACD) for a given ticker symbol.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate MACD for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate MACD for a stock (format: 'YYYY-MM-DD')
        """
        df = await fetch_stock_data(ticker, start_date, end_date)
        close = df['Close']
        macd = MACD(close=close)
        df['MACD'] = macd.macd()
        df['MACD_signal'] = macd.macd_signal()
        df['MACD_hist'] = macd.macd_diff()
        
        
        df.index = df.index.strftime('%Y-%m-%d')
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
        x=df.index, y=df['MACD_hist'], name='MACD Histogram',
        marker_color='gray', opacity=0.3))
        
        fig.add_trace(go.Scatter(
        x=df.index, y=df['MACD'], name='MACD',
        line=dict(color='orange', width=2)))
        
        fig.add_trace(go.Scatter(
        x=df.index, y=df['MACD_signal'], name='Signal',
        line=dict(color='blue', width=2)))
        
        fig.update_layout(
        title=f"{ticker.upper()} - MACD Analysis",
        template='plotly_dark',
        xaxis_title='Date',
        yaxis_title='MACD',
        height=600,
        width=1000
        )
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_macd_chart_{timestamp}.png"
        result= await save_figure_as_base64(fig, filename)
        if "error" in result:
                return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}
        else:
                # Get latest MACD values for the response
                macd_val = df['MACD'].iloc[-1] if not df['MACD'].empty else None
                signal_val = df['MACD_signal'].iloc[-1] if not df['MACD_signal'].empty else None
                macd_signal = "bullish (MACD above signal)" if macd_val and signal_val and macd_val > signal_val else "bearish (MACD below signal)" if macd_val and signal_val else "neutral"
                return {
                "ticker": ticker,
                "chart_generated": True,
                "image_filename": result["filename"],
                "macd_value": round(macd_val, 4) if macd_val else None,
                "signal_value": round(signal_val, 4) if signal_val else None,
                "macd_signal": macd_signal,
                "message": f"MACD chart for {ticker} has been generated and saved to {result['filename']}. Current MACD: {round(macd_val, 4) if macd_val else 'N/A'}, Signal: {round(signal_val, 4) if signal_val else 'N/A'} ({macd_signal})."
                }

# Volume Trends
@technicalanalysis_server.tool(
        name="get_stock_volume",
        description="""Calculate the Volume for the user's given ticker in the given date range.
        Args:
                ticker:str
                        the ticker symbol given by the user for getting Volume of he company.
                start_date:str
                        the start date given by the user for getting Volume of the company.
                end_date:str
                        the end date given by the user for getting Volume of the company.
        """
)
async def get_stock_volume(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the Volume for a given ticker symbol, start date, and end date.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate Volume for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate Volume for a stock (format: 'YYYY-MM-DD')
        """
        df = await fetch_stock_data(ticker, start_date, end_date)
        df['Volume_SMA_20'] = df['Volume'].rolling(window=20).mean()
        
        
        df.index = df.index.strftime('%Y-%m-%d')
        fig = go.Figure()
        fig.add_trace(go.Bar(
        x=df.index, y=df['Volume'], name='Volume',
        marker_color='orange', opacity=0.6))
        
        fig.add_trace(go.Scatter(
        x=df.index, y=df['Volume_SMA_20'], name='20-day SMA',
        line=dict(color='yellow', width=2)))
        
        fig.update_layout(
              title=f"{ticker.upper()} - Volume Trend (with 20-day SMA)",
             template='plotly_dark',
             xaxis_title='Date',
             yaxis_title='Volume',
             height=600,
             width=1000
         )
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_volume_trend_{timestamp}.png"
        result= await save_figure_as_base64(fig, filename)
        if "error" in result:
                return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}
        else:
                # Get latest volume values for the response
                current_volume = df['Volume'].iloc[-1] if not df['Volume'].empty else None
                avg_volume = df['Volume_SMA_20'].iloc[-1] if not df['Volume_SMA_20'].empty else None
                volume_status = "above average" if current_volume and avg_volume and current_volume > avg_volume else "below average" if current_volume and avg_volume else "N/A"
                return {
                "ticker": ticker,
                "chart_generated": True,
                "image_filename": result["filename"],
                "current_volume": int(current_volume) if current_volume else None,
                "avg_volume_20d": int(avg_volume) if avg_volume else None,
                "volume_status": volume_status,
                "message": f"Volume chart for {ticker} has been generated and saved to {result['filename']}. Current volume: {int(current_volume) if current_volume else 'N/A'} ({volume_status} compared to 20-day average)."
                }

# Support/Resistance (using local minima/maxima)
@technicalanalysis_server.tool( 
        name="get_stock_support_resistance",
        description="""Calculate the Support and Resistance levels for the user's given ticker in the given date range.
        Args:
                ticker:str
                        the ticker symbol given by the user for getting Support and Resistance levels of he company.
                start_date:str
                        the start date given by the user for getting Support and Resistance levels of the company.
                end_date:str
                        the end date given by the user for getting Support and Resistance levels of the company.
                """
)
async def get_stock_support_resistance(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the Support and Resistance levels for a given ticker symbol, start date, and end date.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate Support and Resistance levels for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate Support and Resistance levels for a stock (format: 'YYYY-MM-DD')
        """
        df = await fetch_stock_data(ticker, start_date, end_date)
        n = 10  # sensitivity
        df['Support'] = df['Low'][argrelextrema(df['Low'].values, np.less_equal, order=n)[0]]
        df['Resistance'] = df['High'][argrelextrema(df['High'].values, np.greater_equal, order=n)[0]]
        
        
        df.index = df.index.strftime('%Y-%m-%d')
        fig = go.Figure()

        fig.add_trace(go.Scatter(
        x=df.index, y=df['Support'], mode='markers',
        name='Support', marker=dict(color='cyan', size=6, symbol='triangle-down')))
        fig.add_trace(go.Scatter(
        x=df.index, y=df['Resistance'], mode='markers',
        name='Resistance', marker=dict(color='red', size=6, symbol='triangle-up')))

        fig.update_layout(
        title=f'{ticker.upper()} - Support & Resistance Levels',
        height=600,
        template='plotly_dark',
        xaxis_title='Date',
        yaxis_title='Price',
        width=1000
        )
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_support_resistance_{timestamp}.png"
        result = await save_figure_as_base64(fig, filename)
        if "error" in result:
                return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}
        else:
                # Get key support and resistance levels
                support_levels = df['Support'].dropna().tail(3).tolist()
                resistance_levels = df['Resistance'].dropna().tail(3).tolist()
                return {
            "ticker": ticker,
            "chart_generated": True,
            "filename": result["filename"],
            "recent_support_levels": [round(s, 2) for s in support_levels] if support_levels else [],
            "recent_resistance_levels": [round(r, 2) for r in resistance_levels] if resistance_levels else [],
            "message": f"Support and Resistance chart for {ticker} has been generated and saved to {result['filename']}. Key support levels: {[round(s, 2) for s in support_levels] if support_levels else 'N/A'}. Key resistance levels: {[round(r, 2) for r in resistance_levels] if resistance_levels else 'N/A'}."
        }

        
@technicalanalysis_server.tool(
    name="get_all_technical_analysis",
    description="""
    Generate the SMA,RSI (Relative Strength Index),Volume,Support and resistance,MACD and BollingerBands charts for a stock between given dates.

    Args:
        ticker: str - Stock symbol (e.g., "NFLX")
        start_date: str - Start date in 'YYYY-MM-DD'
        end_date: str - End date in 'YYYY-MM-DD'

    Output:
        Base64-encoded chart of SMA,RSI (Relative Strength Index),Volume,Support and resistance,MACD,BollingerBands values with thresholds.
    """
)
async def get_all_technical_analysis(ticker: str, start_date: str, end_date: str) -> dict:
        """Get the SMA,RSI (Relative Strength Index),Volume,Support and resistance,MACD and BollingerBands charts  for a given ticker symbol, start date, and end date.
           Args: str
                ticker: The ticker symbol of the stock
                start_date: The start date to calculate SMA,RSI (Relative Strength Index),Volume,Support and resistance,MACD and BollingerBands  for a stock (format: 'YYYY-MM-DD')
                end_date: The end date to calculate SMA,RSI (Relative Strength Index),Volume,Support and resistance,MACD and BollingerBands for a stock (format: 'YYYY-MM-DD')
        """
        get_stock_sma(ticker, start_date, end_date)
        df = yf.download(ticker, start=start_date, end=end_date)

# Drop multilevel column index if present
        if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

# Indicators Simple Moving Average
        close = df['Close']
        df['SMA_20'] = SMAIndicator(close, window=20).sma_indicator()
        df['SMA_100'] = SMAIndicator(close, window=100).sma_indicator()
        df['SMA_200'] = SMAIndicator(close, window=200).sma_indicator()
        df['SMA_300'] = SMAIndicator(close, window=300).sma_indicator()

#Relative Strength Index – shows if the stock is overbought or oversold
        df['RSI'] = RSIIndicator(close, window=14).rsi()

#Bollinger Bands – show volatility using upper and lower band
        bb = BollingerBands(close=close, window=20, window_dev=2)
        df['Upper_BB'] = bb.bollinger_hband()
        df['Lower_BB'] = bb.bollinger_lband()

#MACD (Moving Average Convergence Divergence) – shows trend changes.
        macd = MACD(close=close)
        df['MACD'] = macd.macd()
        df['MACD_signal'] = macd.macd_signal()

# Volume Trends
        df['Volume_SMA_20'] = df['Volume'].rolling(window=20).mean()

# Support/Resistance (using local minima/maxima)
        n = 10  # sensitivity
        df['Support'] = df['Low'][argrelextrema(df['Low'].values, np.less_equal, order=n)[0]]
        df['Resistance'] = df['High'][argrelextrema(df['High'].values, np.greater_equal, order=n)[0]]

# Subplots
        fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.45, 0.2, 0.2, 0.15],
        subplot_titles=[
        f'{ticker} Price & SMAs',
        'RSI Indicator',
        'MACD',
        'Volume Trends'
        ]
        )


        df.index = df.index.strftime('%Y-%m-%d')
# Candlestick
        fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name='Candlestick'
        ), row=1, col=1)



# Bollinger Bands
        fig.add_trace(go.Scatter(
        x=df.index, y=df['Upper_BB'], name='Upper BB',
        line=dict(color='lightblue', dash='dot')), row=1, col=1)
        fig.add_trace(go.Scatter(
        x=df.index, y=df['Lower_BB'], name='Lower BB',
        line=dict(color='lightblue', dash='dot')), row=1, col=1)

# RSI
        fig.add_trace(go.Scatter(
        x=df.index, y=df['RSI'], name='RSI',
        line=dict(color='lime', width=1)), row=2, col=1)

# Volume
        fig.add_trace(go.Bar(
        x=df.index, y=df['Volume'], name='Volume',
        marker_color='red', opacity=0.4), row=4, col=1)

        '''fig.add_trace(go.Scatter(
         x=df.index, y=df['Volume_SMA_20'], name='Vol SMA 20',
        line=dict(color='yellow', width=1)), row=4, col=1)'''

# SMAs
        for sma_col, color in zip(['SMA_20', 'SMA_100', 'SMA_200', 'SMA_300'],['orange', 'blue', 'green', 'purple']):
                fig.add_trace(go.Scatter(
                x=df.index, y=df[sma_col], name=sma_col,
                line=dict(color=color, width=1.5)), row=1, col=1)

        # Support and Resistance
        fig.add_trace(go.Scatter(
        x=df.index, y=df['Support'], mode='markers',
        name='Support', marker=dict(color='cyan', size=6, symbol='triangle-down')),
        row=1, col=1)
        fig.add_trace(go.Scatter(
        x=df.index, y=df['Resistance'], mode='markers',
        name='Resistance', marker=dict(color='red', size=6, symbol='triangle-up')),
        row=1, col=1)


# MACD
        fig.add_trace(go.Scatter(
        x=df.index, y=df['MACD'], name='MACD',
        line=dict(color='orange')), row=3, col=1)
        '''fig.add_trace(go.Scatter(
        x=df.index, y=df['MACD_signal'], name='MACD Signal',
        line=dict(color='lightblue')), row=3, col=1)'''

# Final layout
        fig.update_layout(
        title=f'{ticker} Technical Analysis (SMA, RSI, MACD, Volume, S/R)',
        height=1000,
        template='plotly_dark',
        xaxis_rangeslider_visible=False,
        showlegend=True,
        
        width=1200
        )
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ticker}_technical_analysis_{timestamp}.png"
        result = await save_figure_as_base64(fig, filename)
        if "error" in result:
                return {"ticker": ticker, "error": result["error"], "traceback": result["traceback"]}
        else:
                # Get key technical indicators for summary
                current_rsi = df['RSI'].iloc[-1] if 'RSI' in df and not df['RSI'].empty else None
                current_macd = df['MACD'].iloc[-1] if 'MACD' in df and not df['MACD'].empty else None
                current_signal = df['MACD_signal'].iloc[-1] if 'MACD_signal' in df and not df['MACD_signal'].empty else None
                current_close = df['Close'].iloc[-1] if 'Close' in df and not df['Close'].empty else None
                sma_20 = df['SMA_20'].iloc[-1] if 'SMA_20' in df and not df['SMA_20'].empty else None
                sma_200 = df['SMA_200'].iloc[-1] if 'SMA_200' in df and not df['SMA_200'].empty else None
                
                # Determine overall technical outlook
                signals = []
                if current_rsi:
                    if current_rsi > 70:
                        signals.append("RSI overbought")
                    elif current_rsi < 30:
                        signals.append("RSI oversold")
                if current_macd and current_signal:
                    if current_macd > current_signal:
                        signals.append("MACD bullish")
                    else:
                        signals.append("MACD bearish")
                if current_close and sma_200:
                    if current_close > sma_200:
                        signals.append("Price above 200-day SMA (bullish)")
                    else:
                        signals.append("Price below 200-day SMA (bearish)")
                
                return {
            "ticker": ticker,
            "chart_generated": True,
            "filename": result["filename"],
            "technical_summary": {
                "rsi": round(current_rsi, 2) if current_rsi else None,
                "macd": round(current_macd, 4) if current_macd else None,
                "macd_signal": round(current_signal, 4) if current_signal else None,
                "current_price": round(current_close, 2) if current_close else None,
                "sma_20": round(sma_20, 2) if sma_20 else None,
                "sma_200": round(sma_200, 2) if sma_200 else None
            },
            "signals": signals,
            "message": f"Comprehensive technical analysis chart for {ticker} has been generated and saved to {result['filename']}. Key signals: {', '.join(signals) if signals else 'Neutral'}."
        }

@technicalanalysis_server.tool(
    name="get_chart_summary",
    description="""
    Generate the detailed summary of the generated chart.

    Args:
        File_path: str - The path to the chart image file.

    Output:
        The summary of the chart, including technical indicators and patterns.
    """
)
async def get_chart_summary(file_path:str) -> dict:
        """This tool reads a stock chart image from the provided file_path,
        sends it to Google's Gemini model, and returns a detailed,
        human-friendly technical analysis.
        Args:
                file_path (str): The path of the chart.
        Returns:
                dict: a dictionary containing the summary of the technical analysis.
        """
        try:
                with open(file_path, "rb") as f:
                        image_bytes = f.read()
                
                # Base64 encode the raw image bytes
                img_b64 = base64.b64encode(image_bytes).decode("utf-8")
                mime = "image/png"
                model = "gemini-2.0-flash"  # Using a more stable model

                if len(image_bytes) > 7 * 1024 * 1024:
                        print("Warning: image >7MB, consider using Files API for better reliability.")

                system_prompt = (
                        "You are an expert financial technical analyst. Produce a structured, "
                        "plain-language summary for retail investors. Include headings: "
                        "Overview, Indicators Detected, Interpretation, Key Levels, "
                        "Trade Ideas (conservative/moderate/aggressive), Confidence & Risks, "
                        "Plain-English Takeaway."
                )

                user_prompt = (
                        f"Analyze the attached chart image containing all the technical indicators. "
                        "1) Identify indicators shown (e.g., SMA, EMA, Bollinger Bands, MACD, RSI). "
                        "2) Explain each indicator's current signal. "
                        "3) List key price levels visible. "
                        "4) Propose 3 trade ideas (entry, stop-loss, target, rationale). "
                        "5) Give confidence level and one-sentence non-technical takeaway."
                )

                api_key = os.getenv("GOOGLE_API_KEY")
                if not api_key:
                        return {
                                "success": False,
                                "error": "GOOGLE_API_KEY not found in environment variables"
                        }
                client = genai.Client(api_key=api_key)

                contents = [
                        {
                        "role": "user",
                        "parts": [
                                {"text": system_prompt},
                                {"inline_data": {"mime_type": mime, "data": img_b64}},
                                {"text": user_prompt}
                        ]
                        }
                ]

                resp = client.models.generate_content(
                        model=model,
                        contents=contents
                )
                summary = resp.text if hasattr(resp, "text") else resp.candidates[0].content.parts[0].text
                return {
                        "success": True,
                        "summary": summary
                }
        except Exception as e:
                return {
                        "success": False,
                        "error": str(e),
                        "traceback": traceback.format_exc()
                }

# ==================== MULTI-TICKER CHARTING TOOLS ====================

@technicalanalysis_server.tool(
        name="get_multi_stock_sma",
        description="""Calculate and plot Simple Moving Averages (SMA) for MULTIPLE stocks on the same chart for comparison.
        Use this when user asks to compare SMA for multiple companies/tickers.
        Args:
                tickers: list[str]
                        List of ticker symbols (e.g., ["AAPL", "MSFT", "GOOGL"])
                start_date: str
                        Start date in format 'YYYY-MM-DD'
                end_date: str
                        End date in format 'YYYY-MM-DD'
        """
)
async def get_multi_stock_sma(tickers: list, start_date: str, end_date: str) -> dict:
        """Generate SMA comparison chart for multiple stocks."""
        try:
                if not isinstance(tickers, list) or len(tickers) < 2:
                        return {"error": "Please provide at least 2 tickers as a list"}
                
                fig = go.Figure()
                colors = ['white', 'cyan', 'yellow', 'magenta', 'lime', 'orange', 'red', 'blue', 'green', 'purple']
                ticker_data = {}
                
                for idx, ticker in enumerate(tickers):
                        try:
                                df = await fetch_stock_data(ticker, start_date, end_date)
                                close = df['Close']
                                df['SMA_20'] = SMAIndicator(close, window=20).sma_indicator()
                                df['SMA_100'] = SMAIndicator(close, window=100).sma_indicator()
                                df['SMA_200'] = SMAIndicator(close, window=200).sma_indicator()
                                
                                df.index = df.index.strftime('%Y-%m-%d')
                                color = colors[idx % len(colors)]
                                
                                # Plot close price
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=close, 
                                        name=f'{ticker.upper()} Close',
                                        line=dict(color=color, width=2)
                                ))
                                
                                # Plot SMAs with lighter shades
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['SMA_20'],
                                        name=f'{ticker.upper()} SMA 20',
                                        line=dict(color=color, width=1, dash='dot'),
                                        opacity=0.6
                                ))
                                
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['SMA_200'],
                                        name=f'{ticker.upper()} SMA 200',
                                        line=dict(color=color, width=1, dash='dash'),
                                        opacity=0.5
                                ))
                                
                                ticker_data[ticker] = {
                                        "current_price": round(close.iloc[-1], 2),
                                        "sma_20": round(df['SMA_20'].iloc[-1], 2) if not df['SMA_20'].empty else None,
                                        "sma_200": round(df['SMA_200'].iloc[-1], 2) if not df['SMA_200'].empty else None
                                }
                        except Exception as e:
                                ticker_data[ticker] = {"error": str(e)}
                
                fig.update_layout(
                        title=f"Multi-Stock SMA Comparison: {', '.join([t.upper() for t in tickers])}",
                        template='plotly_dark',
                        xaxis_title='Date',
                        yaxis_title='Price',
                        height=700,
                        width=1200,
                        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01)
                )
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"multi_stock_sma_{'_'.join(tickers)}_{timestamp}.png"
                result = await save_figure_as_base64(fig, filename, width=1200, height=700)
                
                if "error" in result:
                        return {"tickers": tickers, "error": result["error"], "traceback": result["traceback"]}
                
                return {
                        "tickers": tickers,
                        "chart_generated": True,
                        "image_filename": result["filename"],
                        "ticker_data": ticker_data,
                        "message": f"Multi-stock SMA comparison chart for {', '.join([t.upper() for t in tickers])} has been generated and saved to {result['filename']}."
                }
        except Exception as e:
                return {"tickers": tickers, "error": str(e), "traceback": traceback.format_exc()}


@technicalanalysis_server.tool(
        name="get_multi_stock_rsi",
        description="""Generate RSI (Relative Strength Index) chart for MULTIPLE stocks on the same chart for comparison.
        Use this when user asks to compare RSI for multiple companies/tickers.
        Args:
                tickers: list[str]
                        List of ticker symbols (e.g., ["AAPL", "MSFT"])
                start_date: str
                        Start date in format 'YYYY-MM-DD'
                end_date: str
                        End date in format 'YYYY-MM-DD'
        """
)
async def get_multi_stock_rsi(tickers: list, start_date: str, end_date: str) -> dict:
        """Generate RSI comparison chart for multiple stocks."""
        try:
                if not isinstance(tickers, list) or len(tickers) < 2:
                        return {"error": "Please provide at least 2 tickers as a list"}
                
                fig = go.Figure()
                colors = ['lime', 'cyan', 'yellow', 'magenta', 'orange', 'lightblue', 'pink', 'lightgreen']
                ticker_data = {}
                
                for idx, ticker in enumerate(tickers):
                        try:
                                df = await fetch_stock_data(ticker, start_date, end_date)
                                close = df['Close']
                                rsi = RSIIndicator(close=close, window=14)
                                df['RSI'] = rsi.rsi()
                                
                                df.index = df.index.strftime('%Y-%m-%d')
                                color = colors[idx % len(colors)]
                                
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['RSI'],
                                        name=f'{ticker.upper()} RSI',
                                        line=dict(color=color, width=2)
                                ))
                                
                                current_rsi = df['RSI'].iloc[-1] if not df['RSI'].empty else None
                                rsi_status = "overbought" if current_rsi and current_rsi > 70 else "oversold" if current_rsi and current_rsi < 30 else "neutral"
                                ticker_data[ticker] = {
                                        "current_rsi": round(current_rsi, 2) if current_rsi else None,
                                        "status": rsi_status
                                }
                        except Exception as e:
                                ticker_data[ticker] = {"error": str(e)}
                
                # Add overbought/oversold reference lines
                fig.add_shape(type="line", x0=0, x1=1, xref='paper', y0=70, y1=70,
                             line=dict(color="red", dash="dash", width=1))
                fig.add_shape(type="line", x0=0, x1=1, xref='paper', y0=30, y1=30,
                             line=dict(color="blue", dash="dash", width=1))
                
                fig.update_layout(
                        title=f"Multi-Stock RSI Comparison: {', '.join([t.upper() for t in tickers])}",
                        template='plotly_dark',
                        xaxis_title='Date',
                        yaxis_title='RSI Value',
                        yaxis=dict(range=[0, 100]),
                        height=600,
                        width=1200,
                        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01)
                )
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"multi_stock_rsi_{'_'.join(tickers)}_{timestamp}.png"
                result = await save_figure_as_base64(fig, filename, width=1200, height=600)
                
                if "error" in result:
                        return {"tickers": tickers, "error": result["error"], "traceback": result["traceback"]}
                
                return {
                        "tickers": tickers,
                        "chart_generated": True,
                        "image_filename": result["filename"],
                        "ticker_data": ticker_data,
                        "message": f"Multi-stock RSI comparison chart for {', '.join([t.upper() for t in tickers])} has been generated and saved to {result['filename']}."
                }
        except Exception as e:
                return {"tickers": tickers, "error": str(e), "traceback": traceback.format_exc()}


@technicalanalysis_server.tool(
        name="get_multi_stock_macd",
        description="""Calculate and plot MACD for MULTIPLE stocks on the same chart for comparison.
        Use this when user asks to compare MACD for multiple companies/tickers.
        Args:
                tickers: list[str]
                        List of ticker symbols (e.g., ["AAPL", "TSLA"])
                start_date: str
                        Start date in format 'YYYY-MM-DD'
                end_date: str
                        End date in format 'YYYY-MM-DD'
        """
)
async def get_multi_stock_macd(tickers: list, start_date: str, end_date: str) -> dict:
        """Generate MACD comparison chart for multiple stocks."""
        try:
                if not isinstance(tickers, list) or len(tickers) < 2:
                        return {"error": "Please provide at least 2 tickers as a list"}
                
                fig = go.Figure()
                colors = ['orange', 'cyan', 'lime', 'magenta', 'yellow', 'lightblue', 'pink']
                ticker_data = {}
                
                for idx, ticker in enumerate(tickers):
                        try:
                                df = await fetch_stock_data(ticker, start_date, end_date)
                                close = df['Close']
                                macd = MACD(close=close)
                                df['MACD'] = macd.macd()
                                df['MACD_signal'] = macd.macd_signal()
                                
                                df.index = df.index.strftime('%Y-%m-%d')
                                color = colors[idx % len(colors)]
                                
                                # Plot MACD line
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['MACD'],
                                        name=f'{ticker.upper()} MACD',
                                        line=dict(color=color, width=2)
                                ))
                                
                                # Plot signal line with lighter shade
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['MACD_signal'],
                                        name=f'{ticker.upper()} Signal',
                                        line=dict(color=color, width=1, dash='dash'),
                                        opacity=0.5
                                ))
                                
                                macd_val = df['MACD'].iloc[-1] if not df['MACD'].empty else None
                                signal_val = df['MACD_signal'].iloc[-1] if not df['MACD_signal'].empty else None
                                macd_signal = "bullish" if macd_val and signal_val and macd_val > signal_val else "bearish"
                                
                                ticker_data[ticker] = {
                                        "macd_value": round(macd_val, 4) if macd_val else None,
                                        "signal_value": round(signal_val, 4) if signal_val else None,
                                        "signal": macd_signal
                                }
                        except Exception as e:
                                ticker_data[ticker] = {"error": str(e)}
                
                fig.update_layout(
                        title=f"Multi-Stock MACD Comparison: {', '.join([t.upper() for t in tickers])}",
                        template='plotly_dark',
                        xaxis_title='Date',
                        yaxis_title='MACD',
                        height=700,
                        width=1200,
                        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01)
                )
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"multi_stock_macd_{'_'.join(tickers)}_{timestamp}.png"
                result = await save_figure_as_base64(fig, filename, width=1200, height=700)
                
                if "error" in result:
                        return {"tickers": tickers, "error": result["error"], "traceback": result["traceback"]}
                
                return {
                        "tickers": tickers,
                        "chart_generated": True,
                        "image_filename": result["filename"],
                        "ticker_data": ticker_data,
                        "message": f"Multi-stock MACD comparison chart for {', '.join([t.upper() for t in tickers])} has been generated and saved to {result['filename']}."
                }
        except Exception as e:
                return {"tickers": tickers, "error": str(e), "traceback": traceback.format_exc()}


@technicalanalysis_server.tool(
        name="get_multi_stock_bollingerbands",
        description="""Calculate and plot Bollinger Bands for MULTIPLE stocks on the same chart for comparison.
        Use this when user asks to compare Bollinger Bands for multiple companies/tickers.
        Args:
                tickers: list[str]
                        List of ticker symbols (e.g., ["AAPL", "GOOGL"])
                start_date: str
                        Start date in format 'YYYY-MM-DD'
                end_date: str
                        End date in format 'YYYY-MM-DD'
        """
)
async def get_multi_stock_bollingerbands(tickers: list, start_date: str, end_date: str) -> dict:
        """Generate Bollinger Bands comparison chart for multiple stocks."""
        try:
                if not isinstance(tickers, list) or len(tickers) < 2:
                        return {"error": "Please provide at least 2 tickers as a list"}
                
                fig = go.Figure()
                colors = ['cyan', 'yellow', 'lime', 'magenta', 'orange', 'pink', 'lightblue']
                ticker_data = {}
                
                for idx, ticker in enumerate(tickers):
                        try:
                                df = await fetch_stock_data(ticker, start_date, end_date)
                                close = df['Close']
                                bb = BollingerBands(close=close, window=20, window_dev=2)
                                df['Upper_BB'] = bb.bollinger_hband()
                                df['Lower_BB'] = bb.bollinger_lband()
                                df['SMA_20'] = bb.bollinger_mavg()
                                
                                df.index = df.index.strftime('%Y-%m-%d')
                                color = colors[idx % len(colors)]
                                
                                # Plot upper band
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['Upper_BB'],
                                        name=f'{ticker.upper()} Upper BB',
                                        line=dict(color=color, dash='dot', width=1),
                                        opacity=0.5
                                ))
                                
                                # Plot lower band
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['Lower_BB'],
                                        name=f'{ticker.upper()} Lower BB',
                                        line=dict(color=color, dash='dot', width=1),
                                        opacity=0.5
                                ))
                                
                                # Plot SMA (middle band)
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['SMA_20'],
                                        name=f'{ticker.upper()} SMA 20',
                                        line=dict(color=color, width=2)
                                ))
                                
                                ticker_data[ticker] = {
                                        "upper_band": round(df['Upper_BB'].iloc[-1], 2) if not df['Upper_BB'].empty else None,
                                        "lower_band": round(df['Lower_BB'].iloc[-1], 2) if not df['Lower_BB'].empty else None,
                                        "sma_20": round(df['SMA_20'].iloc[-1], 2) if not df['SMA_20'].empty else None
                                }
                        except Exception as e:
                                ticker_data[ticker] = {"error": str(e)}
                
                fig.update_layout(
                        title=f"Multi-Stock Bollinger Bands Comparison: {', '.join([t.upper() for t in tickers])}",
                        template='plotly_dark',
                        xaxis_title='Date',
                        yaxis_title='Price',
                        height=700,
                        width=1200,
                        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01)
                )
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"multi_stock_bb_{'_'.join(tickers)}_{timestamp}.png"
                result = await save_figure_as_base64(fig, filename, width=1200, height=700)
                
                if "error" in result:
                        return {"tickers": tickers, "error": result["error"], "traceback": result["traceback"]}
                
                return {
                        "tickers": tickers,
                        "chart_generated": True,
                        "image_filename": result["filename"],
                        "ticker_data": ticker_data,
                        "message": f"Multi-stock Bollinger Bands comparison chart for {', '.join([t.upper() for t in tickers])} has been generated and saved to {result['filename']}."
                }
        except Exception as e:
                return {"tickers": tickers, "error": str(e), "traceback": traceback.format_exc()}


@technicalanalysis_server.tool(
        name="get_multi_stock_volume",
        description="""Calculate and plot Volume trends for MULTIPLE stocks on the same chart for comparison.
        Use this when user asks to compare Volume for multiple companies/tickers.
        Args:
                tickers: list[str]
                        List of ticker symbols (e.g., ["AAPL", "MSFT"])
                start_date: str
                        Start date in format 'YYYY-MM-DD'
                end_date: str
                        End date in format 'YYYY-MM-DD'
        """
)
async def get_multi_stock_volume(tickers: list, start_date: str, end_date: str) -> dict:
        """Generate Volume comparison chart for multiple stocks."""
        try:
                if not isinstance(tickers, list) or len(tickers) < 2:
                        return {"error": "Please provide at least 2 tickers as a list"}
                
                # Create subplots for each ticker
                fig = make_subplots(
                        rows=len(tickers), cols=1,
                        shared_xaxes=True,
                        vertical_spacing=0.05,
                        subplot_titles=[f'{ticker.upper()} Volume' for ticker in tickers]
                )
                
                colors = ['orange', 'cyan', 'lime', 'magenta', 'yellow', 'pink', 'lightblue']
                ticker_data = {}
                
                for idx, ticker in enumerate(tickers):
                        try:
                                df = await fetch_stock_data(ticker, start_date, end_date)
                                df['Volume_SMA_20'] = df['Volume'].rolling(window=20).mean()
                                
                                df.index = df.index.strftime('%Y-%m-%d')
                                color = colors[idx % len(colors)]
                                row = idx + 1
                                
                                # Add volume bars
                                fig.add_trace(go.Bar(
                                        x=df.index, y=df['Volume'],
                                        name=f'{ticker.upper()} Volume',
                                        marker_color=color,
                                        opacity=0.6
                                ), row=row, col=1)
                                
                                # Add SMA line
                                fig.add_trace(go.Scatter(
                                        x=df.index, y=df['Volume_SMA_20'],
                                        name=f'{ticker.upper()} 20-day SMA',
                                        line=dict(color=color, width=2)
                                ), row=row, col=1)
                                
                                current_volume = df['Volume'].iloc[-1] if not df['Volume'].empty else None
                                avg_volume = df['Volume_SMA_20'].iloc[-1] if not df['Volume_SMA_20'].empty else None
                                
                                ticker_data[ticker] = {
                                        "current_volume": int(current_volume) if current_volume else None,
                                        "avg_volume_20d": int(avg_volume) if avg_volume else None,
                                        "status": "above average" if current_volume and avg_volume and current_volume > avg_volume else "below average"
                                }
                        except Exception as e:
                                ticker_data[ticker] = {"error": str(e)}
                
                fig.update_layout(
                        title=f"Multi-Stock Volume Comparison: {', '.join([t.upper() for t in tickers])}",
                        template='plotly_dark',
                        height=400 * len(tickers),
                        width=1200,
                        showlegend=True
                )
                
                fig.update_xaxes(title_text='Date', row=len(tickers), col=1)
                for i in range(len(tickers)):
                        fig.update_yaxes(title_text='Volume', row=i+1, col=1)
                
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"multi_stock_volume_{'_'.join(tickers)}_{timestamp}.png"
                result = await save_figure_as_base64(fig, filename, width=1200, height=400*len(tickers))
                
                if "error" in result:
                        return {"tickers": tickers, "error": result["error"], "traceback": result["traceback"]}
                
                return {
                        "tickers": tickers,
                        "chart_generated": True,
                        "image_filename": result["filename"],
                        "ticker_data": ticker_data,
                        "message": f"Multi-stock Volume comparison chart for {', '.join([t.upper() for t in tickers])} has been generated and saved to {result['filename']}."
                }
        except Exception as e:
                return {"tickers": tickers, "error": str(e), "traceback": traceback.format_exc()}
    
if __name__ == "__main__":
        import asyncio
        if sys.platform.startswith("win"):
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        print("Launching TradingView MCP Server...")
        technicalanalysis_server.run(transport="streamable-http", port=8566)