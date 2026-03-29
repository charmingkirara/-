"""
Multi-Timeframe Analysis
────────────────────────────────────────────────────────────────────────────────
Fetches multiple timeframes and calculates:
  - HTF (H1, H4) trend direction for entry filter
  - 戻り高値 / 押安値 (pullback highs/lows) on each timeframe:
      H1  : 1時間戻り高値 / 1時間押安値
      M15 : 15分戻り高値  / 15分押安値
      M5  : 5分戻り高値   / 5分押安値

These levels act as key S/R zones shown on the chart annotations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from trading_bot import config
from trading_bot.indicators import (
    calculate_ma,
    find_swing_highs,
    find_swing_lows,
    ma_direction,
    calculate_rsi,
)

logger = logging.getLogger(__name__)


# ── Data Structures ────────────────────────────────────────────────────────────

@dataclass
class TFLevels:
    """Key price levels derived from a single timeframe."""
    granularity: str
    trend: str                         # 'up', 'down', 'flat'
    pullback_high: Optional[float]     # 戻り高値（直近スイング高値）
    pullback_low: Optional[float]      # 押安値（直近スイング安値）
    ma3: Optional[float]               # 3MA on this TF
    rsi: Optional[float]               # RSI on this TF


@dataclass
class MultiTFContext:
    """Aggregated multi-timeframe context passed to the strategy."""
    m5:  Optional[TFLevels] = None
    m15: Optional[TFLevels] = None    # primary timeframe
    h1:  Optional[TFLevels] = None
    h4:  Optional[TFLevels] = None

    # Convenience: HTF bias from H1 (primary filter)
    @property
    def h1_trend(self) -> str:
        return self.h1.trend if self.h1 else "flat"

    @property
    def h4_trend(self) -> str:
        return self.h4.trend if self.h4 else "flat"

    def key_resistances(self) -> list[float]:
        """All pullback highs across timeframes, sorted descending."""
        levels = []
        for tf in (self.h4, self.h1, self.m15, self.m5):
            if tf and tf.pullback_high:
                levels.append(tf.pullback_high)
        return sorted(set(levels), reverse=True)

    def key_supports(self) -> list[float]:
        """All pullback lows across timeframes, sorted ascending."""
        levels = []
        for tf in (self.h4, self.h1, self.m15, self.m5):
            if tf and tf.pullback_low:
                levels.append(tf.pullback_low)
        return sorted(set(levels))

    def htf_allows_long(self) -> bool:
        """
        H1 trend must be up (or flat = neutral) to allow long entries.
        H4 must not be strongly bearish.
        """
        h1_ok = self.h1_trend in ("up", "flat")
        h4_ok = self.h4_trend != "down"
        return h1_ok and h4_ok

    def htf_allows_short(self) -> bool:
        """
        H1 trend must be down (or flat) to allow short entries.
        H4 must not be strongly bullish.
        """
        h1_ok = self.h1_trend in ("down", "flat")
        h4_ok = self.h4_trend != "up"
        return h1_ok and h4_ok


# ── Single-TF Analysis ─────────────────────────────────────────────────────────

def _analyze_tf(df: pd.DataFrame, granularity: str, lookback: int = 5) -> TFLevels:
    """
    Compute trend direction and key levels for one timeframe.
    """
    if df is None or len(df) < lookback * 2 + 5:
        return TFLevels(
            granularity=granularity,
            trend="flat",
            pullback_high=None,
            pullback_low=None,
            ma3=None,
            rsi=None,
        )

    close = df["close"]
    highs = df["high"]
    lows = df["low"]

    ma = calculate_ma(close, config.MA_PERIOD)
    rsi_series = calculate_rsi(close, config.RSI_PERIOD)
    direction = ma_direction(ma)

    # Swing high/low for pullback levels
    sh_mask = find_swing_highs(highs, lookback)
    sl_mask = find_swing_lows(lows, lookback)

    sh_list = highs[sh_mask].tolist()
    sl_list = lows[sl_mask].tolist()

    pullback_high = sh_list[-1] if sh_list else None
    pullback_low = sl_list[-1] if sl_list else None
    latest_ma = float(ma.iloc[-1]) if not ma.empty else None
    latest_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else None

    return TFLevels(
        granularity=granularity,
        trend=direction,
        pullback_high=pullback_high,
        pullback_low=pullback_low,
        ma3=latest_ma,
        rsi=latest_rsi,
    )


# ── Multi-TF Builder ───────────────────────────────────────────────────────────

def build_mtf_context(broker) -> MultiTFContext:
    """
    Fetch all required timeframes from the broker and build MultiTFContext.

    Parameters
    ----------
    broker : OANDABroker instance (duck-typed — only needs .fetch_candles())
    """
    ctx = MultiTFContext()

    timeframe_map = {
        config.TF_M5:  "m5",
        config.TF_H1:  "h1",
        config.TF_H4:  "h4",
    }

    for granularity, attr in timeframe_map.items():
        df = broker.fetch_candles(
            granularity=granularity,
            count=config.HTF_CANDLE_COUNT,
        )
        if df is not None and not df.empty:
            levels = _analyze_tf(df, granularity)
            setattr(ctx, attr, levels)
            logger.info(
                "MTF [%s]: trend=%s  pullback_H=%.3f  pullback_L=%.3f",
                granularity, levels.trend,
                levels.pullback_high or 0.0,
                levels.pullback_low or 0.0,
            )
        else:
            logger.warning("MTF [%s]: failed to fetch data.", granularity)

    return ctx


# ── Nearest Level Helpers ──────────────────────────────────────────────────────

def nearest_resistance(price: float, ctx: MultiTFContext, max_distance_pips: float = 50) -> Optional[float]:
    """Return the nearest resistance level above current price within range."""
    pip = config.PIP_VALUE_JPY
    candidates = [r for r in ctx.key_resistances() if r > price and (r - price) <= max_distance_pips * pip]
    return candidates[-1] if candidates else None  # lowest one above price


def nearest_support(price: float, ctx: MultiTFContext, max_distance_pips: float = 50) -> Optional[float]:
    """Return the nearest support level below current price within range."""
    pip = config.PIP_VALUE_JPY
    candidates = [s for s in ctx.key_supports() if s < price and (price - s) <= max_distance_pips * pip]
    return candidates[0] if candidates else None   # highest one below price
