"""
Technical indicators for the USD/JPY 15-minute strategy.

Implements:
  - 3MA  : 3-period simple moving average of close
  - RSI  : 14-period Relative Strength Index
  - Wave : Swing high/low detection for wave pattern analysis
  - Channel : Parallel channel detection (上昇チャネル / 下降チャネル)
"""

import numpy as np
import pandas as pd


# ── Moving Average ─────────────────────────────────────────────────────────────

def calculate_ma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period).mean()


def ma_direction(ma: pd.Series, lookback: int = 3) -> str:
    """
    Determine 3MA direction over the last `lookback` bars.
    Returns 'up', 'down', or 'flat'.
    """
    if len(ma) < lookback + 1:
        return "flat"
    recent = ma.dropna().iloc[-lookback:]
    if len(recent) < 2:
        return "flat"
    slope = recent.iloc[-1] - recent.iloc[0]
    threshold = 0.005  # ~0.5 pip noise filter for JPY pairs
    if slope > threshold:
        return "up"
    if slope < -threshold:
        return "down"
    return "flat"


def price_vs_ma(close: float, ma: float) -> str:
    """Returns 'above', 'below', or 'at' relative to MA."""
    diff = close - ma
    if abs(diff) < 0.005:
        return "at"
    return "above" if diff > 0 else "below"


# ── RSI ────────────────────────────────────────────────────────────────────────

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI.
    Returns a Series of RSI values (0-100).
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def rsi_signal(rsi_value: float, upper: float = 70, lower: float = 30, mid: float = 50) -> str:
    """
    Classify current RSI into a trading signal.

    Returns:
      'overbought'  – RSI >= upper  (potential short setup forming)
      'oversold'    – RSI <= lower  (potential long setup forming)
      'bullish'     – 50 < RSI < upper
      'bearish'     – lower < RSI < 50
      'neutral'     – exactly at 50
    """
    if rsi_value >= upper:
        return "overbought"
    if rsi_value <= lower:
        return "oversold"
    if rsi_value > mid:
        return "bullish"
    if rsi_value < mid:
        return "bearish"
    return "neutral"


# ── Wave / Swing Detection ─────────────────────────────────────────────────────

def find_swing_highs(highs: pd.Series, lookback: int = 5) -> pd.Series:
    """
    Mark swing highs: a bar whose high is the highest in the window
    [i-lookback .. i+lookback].
    Returns a boolean Series.
    """
    result = pd.Series(False, index=highs.index)
    for i in range(lookback, len(highs) - lookback):
        window = highs.iloc[i - lookback: i + lookback + 1]
        if highs.iloc[i] == window.max():
            result.iloc[i] = True
    return result


def find_swing_lows(lows: pd.Series, lookback: int = 5) -> pd.Series:
    """
    Mark swing lows: a bar whose low is the lowest in the window.
    Returns a boolean Series.
    """
    result = pd.Series(False, index=lows.index)
    for i in range(lookback, len(lows) - lookback):
        window = lows.iloc[i - lookback: i + lookback + 1]
        if lows.iloc[i] == window.min():
            result.iloc[i] = True
    return result


def detect_wave_pattern(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Analyse the swing structure of the most recent bars.

    Returns a dict with:
      trend       : 'uptrend' | 'downtrend' | 'range'
      last_swing_high : float
      last_swing_low  : float
      higher_high : bool
      higher_low  : bool
      lower_high  : bool
      lower_low   : bool
    """
    highs = df["high"]
    lows = df["low"]

    swing_high_mask = find_swing_highs(highs, lookback)
    swing_low_mask = find_swing_lows(lows, lookback)

    swing_highs = highs[swing_high_mask].tolist()
    swing_lows = lows[swing_low_mask].tolist()

    result = {
        "trend": "range",
        "last_swing_high": swing_highs[-1] if swing_highs else None,
        "last_swing_low": swing_lows[-1] if swing_lows else None,
        "higher_high": False,
        "higher_low": False,
        "lower_high": False,
        "lower_low": False,
    }

    # Need at least 2 of each to compare
    if len(swing_highs) >= 2:
        result["higher_high"] = swing_highs[-1] > swing_highs[-2]
        result["lower_high"] = swing_highs[-1] < swing_highs[-2]

    if len(swing_lows) >= 2:
        result["higher_low"] = swing_lows[-1] > swing_lows[-2]
        result["lower_low"] = swing_lows[-1] < swing_lows[-2]

    # Uptrend: higher highs AND higher lows
    if result["higher_high"] and result["higher_low"]:
        result["trend"] = "uptrend"
    # Downtrend: lower highs AND lower lows
    elif result["lower_high"] and result["lower_low"]:
        result["trend"] = "downtrend"
    else:
        result["trend"] = "range"

    return result


# ── Channel Detection ──────────────────────────────────────────────────────────

def detect_channel(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Fit a simple linear channel through recent swing highs and lows.

    Returns:
      slope       : float (positive = ascending, negative = descending)
      upper_level : float (projected channel top at current bar)
      lower_level : float (projected channel bottom at current bar)
      channel_type: 'ascending' | 'descending' | 'flat'
    """
    highs = df["high"]
    lows = df["low"]

    swing_high_mask = find_swing_highs(highs, lookback)
    swing_low_mask = find_swing_lows(lows, lookback)

    sh_idx = np.where(swing_high_mask)[0]
    sl_idx = np.where(swing_low_mask)[0]

    result = {
        "slope": 0.0,
        "upper_level": highs.iloc[-1],
        "lower_level": lows.iloc[-1],
        "channel_type": "flat",
    }

    if len(sh_idx) >= 2:
        x = sh_idx[-2:]
        y = highs.iloc[sh_idx[-2:]].values
        slope_high = (y[-1] - y[0]) / max(x[-1] - x[0], 1)
        n = len(df) - 1
        result["upper_level"] = y[-1] + slope_high * (n - x[-1])
    else:
        slope_high = 0.0

    if len(sl_idx) >= 2:
        x = sl_idx[-2:]
        y = lows.iloc[sl_idx[-2:]].values
        slope_low = (y[-1] - y[0]) / max(x[-1] - x[0], 1)
        n = len(df) - 1
        result["lower_level"] = y[-1] + slope_low * (n - x[-1])
    else:
        slope_low = 0.0

    result["slope"] = (slope_high + slope_low) / 2
    if result["slope"] > 0.0005:
        result["channel_type"] = "ascending"
    elif result["slope"] < -0.0005:
        result["channel_type"] = "descending"

    return result


# ── Horizontal Level Detection ─────────────────────────────────────────────────

def find_key_levels(df: pd.DataFrame, lookback: int = 5, tolerance_pips: float = 0.10) -> list:
    """
    Identify horizontal support/resistance levels from swing points.
    Levels that have been tested multiple times are stronger.

    Returns a list of price levels sorted descending.
    """
    highs = df["high"]
    lows = df["low"]

    swing_high_mask = find_swing_highs(highs, lookback)
    swing_low_mask = find_swing_lows(lows, lookback)

    candidates = list(highs[swing_high_mask]) + list(lows[swing_low_mask])
    if not candidates:
        return []

    # Cluster nearby levels
    candidates.sort()
    levels = []
    current_cluster = [candidates[0]]
    for price in candidates[1:]:
        if price - current_cluster[-1] <= tolerance_pips:
            current_cluster.append(price)
        else:
            levels.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [price]
    levels.append(sum(current_cluster) / len(current_cluster))

    return sorted(levels, reverse=True)


# ── All-in-one signal aggregation ─────────────────────────────────────────────

def compute_all_indicators(df: pd.DataFrame, ma_period: int = 3, rsi_period: int = 14,
                            wave_lookback: int = 5) -> dict:
    """
    Compute all indicators on the given OHLC DataFrame.

    DataFrame must have columns: open, high, low, close (float).
    Returns a dict of all computed values for the latest bar.
    """
    close = df["close"]

    ma = calculate_ma(close, ma_period)
    rsi = calculate_rsi(close, rsi_period)
    wave = detect_wave_pattern(df, wave_lookback)
    channel = detect_channel(df, wave_lookback)
    key_levels = find_key_levels(df, wave_lookback)

    latest_close = close.iloc[-1]
    latest_ma = ma.iloc[-1]
    latest_rsi = rsi.iloc[-1]

    return {
        # Raw series (last value)
        "close": latest_close,
        "ma3": latest_ma,
        "rsi": latest_rsi,
        # Derived
        "ma_direction": ma_direction(ma),
        "price_vs_ma": price_vs_ma(latest_close, latest_ma) if not np.isnan(latest_ma) else "unknown",
        "rsi_signal": rsi_signal(latest_rsi),
        # Wave
        "wave_trend": wave["trend"],
        "higher_high": wave["higher_high"],
        "higher_low": wave["higher_low"],
        "lower_high": wave["lower_high"],
        "lower_low": wave["lower_low"],
        "last_swing_high": wave["last_swing_high"],
        "last_swing_low": wave["last_swing_low"],
        # Channel
        "channel_type": channel["channel_type"],
        "channel_upper": channel["upper_level"],
        "channel_lower": channel["lower_level"],
        # Key horizontal levels
        "key_levels": key_levels,
    }
