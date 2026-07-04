import os
import logging
from typing import Any

# NY timezone for candle timestamp parsing
try:
    from zoneinfo import ZoneInfo
    CHART_NY_TZ = ZoneInfo("America/New_York")
except ImportError:
    from datetime import timedelta, timezone
    CHART_NY_TZ = timezone(timedelta(hours=-5))

logger = logging.getLogger("chart_generator")

# =====================================================================
# GLOBAL TOGGLE: Set to False to completely disable chart generation
# and bypass importing heavy libraries (matplotlib, pandas, etc.)
# =====================================================================
ENABLE_CHART_GENERATION: bool = False

# Conditional imports to prevent crashes on devices without heavy dependencies when disabled
if ENABLE_CHART_GENERATION:
    try:
        import matplotlib
        matplotlib.use('Agg')  # Use headless backend to prevent GUI/browser errors
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
        import pandas as pd
    except ImportError as e:
        logger.error(f"Failed to import chart dependencies: {e}. Disabling chart generation automatically.")
        ENABLE_CHART_GENERATION = False


def generate_candlestick_chart(csv_data: str, ticker: str, timeframe: str, output_path: str, limit: int = 80) -> bool:
    """
    Generates an ultra-premium, clean, light-themed candlestick chart with zero gridlines
    using Matplotlib (runs entirely in memory, no browser/Chromium required).

    Args:
        csv_data (str): The raw CSV data containing candle information.
        ticker (str): The ticker symbol (e.g., "EURUSD").
        timeframe (str): The timeframe of the candles (e.g., "1h").
        output_path (str): The file path where the generated chart image will be saved.
        limit (int): The maximum number of candles to plot. Defaults to 80.

    Returns:
        bool: True if the chart was successfully generated and saved, False otherwise.
    """
    if not ENABLE_CHART_GENERATION:
        logger.info("Chart generation is disabled globally. Skipping.")
        return False

    try:
        logger.info(f"Starting chart generation for {ticker} ({timeframe}). Output path: {output_path}")
        if not csv_data or not csv_data.strip():
            logger.warning("Empty CSV data provided for chart generation.")
            return False

        # Log a small sample of the incoming data for debugging
        logger.info(f"Received csv_data sample (first 150 chars): {csv_data[:150]!r}")

        # Normalize line endings for Linux/Windows compatibility
        csv_data_normalized = csv_data.replace("\r\n", "\n").replace("\r", "\n")
        lines = csv_data_normalized.split("\n")
        
        data_rows = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Dynamically detect delimiter (comma, semicolon, or tab)
            delimiter = ","
            if ";" in line and line.count(";") > line.count(","):
                delimiter = ";"
            elif "\t" in line and line.count("\t") > line.count(","):
                delimiter = "\t"

            parts = [p.strip().strip('"').strip("'") for p in line.split(delimiter)]
            if len(parts) >= 5:
                try:
                    # Parse timestamp (supports Unix timestamp or date/datetime strings)
                    try:
                        t_val = int(float(parts[0]))
                    except ValueError:
                        # Fallback: Try parsing as a datetime string (NY local time)
                        t_val = int(pd.to_datetime(parts[0]).tz_localize(CHART_NY_TZ).timestamp())

                    o_val = float(parts[1])
                    h_val = float(parts[2])
                    l_val = float(parts[3])
                    c_val = float(parts[4])
                    
                    data_rows.append({
                        "t": t_val,
                        "o": o_val,
                        "h": h_val,
                        "l": l_val,
                        "c": c_val
                    })
                except Exception as parse_ex:
                    # Skip headers, metadata, or unparseable lines silently
                    continue
        
        logger.info(f"Parsed {len(data_rows)} valid data rows.")
        if len(data_rows) < 2:
            logger.warning(f"Insufficient valid data rows parsed ({len(data_rows)}) to generate chart. Raw data sample: {csv_data[:300]!r}")
            return False

        # Limit to the exact number of candles requested by the tool
        if limit and limit > 0:
            data_rows = data_rows[-limit:]

        df = pd.DataFrame(data_rows)
        df['date'] = pd.to_datetime(df['t'], unit='s')\
            .tz_localize('UTC').tz_convert(CHART_NY_TZ).tz_localize(None)
        
        # Setup premium light theme colors
        bg_color = "#FFFFFF"       # White background
        bull_color = "#FFFFFF"     # White bullish body
        bear_color = "#000000"     # Black bearish body
        wick_color = "#000000"     # Black wicks and borders
        text_color = "#000000"     # Black text for labels
        
        # Create figure and axis
        fig, ax = plt.subplots(figsize=(11, 6), facecolor=bg_color)
        ax.set_facecolor(bg_color)
        
        # Plot candlesticks manually for maximum performance and styling control
        width = 0.6 * (df['date'].diff().min().total_seconds() / 86400.0) if len(df) > 1 else 0.1
        
        for idx, row in df.iterrows():
            t = matplotlib.dates.date2num(row['date'])
            o, h, l, c = row['o'], row['h'], row['l'], row['c']
            color = bull_color if c >= o else bear_color
            
            # Draw wick (shadow) in black
            ax.plot([t, t], [l, h], color=wick_color, linewidth=1.2, zorder=2)
            
            # Draw body
            body_bottom = min(o, c)
            body_height = abs(o - c)
            if body_height == 0:
                body_height = 0.0001  # Prevent invisible flat candles
                
            rect = patches.Rectangle(
                (t - width/2, body_bottom),
                width,
                body_height,
                facecolor=color,
                edgecolor=wick_color,
                linewidth=1.0,
                zorder=3
            )
            ax.add_patch(rect)

        # Format X-axis with dates
        ax.xaxis_date()
        fig.autofmt_xdate()
        
        # Completely remove all gridlines, borders, and spines
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
            
        # Style tick labels
        ax.tick_params(colors=text_color, labelsize=10, length=0)
        
        # Move Y-axis to the right side (TradingView style)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        
        # Dynamic precision formatting for Y-axis
        max_val = df['c'].max()
        if max_val < 2.0:
            ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter('%.5f'))
        else:
            ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter('%.2f'))

        # Add premium title (No emoji to prevent Glyph warning)
        plt.title(
            f"{ticker.upper()} — {timeframe.upper()} Analysis",
            color="#000000",
            fontsize=16,
            fontweight="bold",
            pad=20,
            loc="left"
        )
        
        # Add subtle background watermark
        fig.text(
            0.5, 0.5,
            "OXYGPT",
            fontsize=40,
            color="#000000",
            weight="bold",
            alpha=0.03,
            ha="center",
            va="center",
            rotation=15
        )
        
        # Ensure output directory exists
        dir_name = os.path.dirname(output_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            logger.info(f"Ensured directory exists: {dir_name}")

        # Save the figure directly to disk with lower DPI for faster generation
        plt.savefig(
            output_path,
            facecolor=bg_color,
            edgecolor='none',
            bbox_inches='tight',
            dpi=80
        )
        plt.close(fig)
        
        if os.path.exists(output_path):
            logger.info(f"Successfully generated and saved chart to {output_path} (Size: {os.path.getsize(output_path)} bytes)")
            return True
        else:
            logger.error(f"Chart file was not found on disk after save attempt: {output_path}")
            return False
    except Exception as e:
        logger.error(f"Failed to generate candlestick chart: {e}", exc_info=True)
        return False
