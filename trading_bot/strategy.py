"""
USD/JPY 15-minute trading strategy.

Entry logic (all 3 must align):
  1. 3MA direction  : price above rising 3MA (long) / below falling 3MA (short)
  2. Wave pattern   : higher highs + higher lows (long) / lower highs + lower lows (short)
  3. RSI            : bullish zone >50 (long) / bearish zone <50 (short)

徹底事項 (strict rules):
  - 夜中はエントリーしない       No night entries (22:00–07:00 JST)
  - シグナルだけで入らない       All 3 conditions must align
  - よくわからない根拠で入らない  No ambiguous setups (trend must be clear)
  - 損切りの根拠を持たせる       20 pip stop loss, placed beyond swing high/low
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import pandas as pd

from trading_bot import config
from trading_bot.indicators import compute_all_indicators

logger = logging.getLogger(__name__)


class Signal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass
class TradeSetup:
    signal: Signal
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    indicators: dict = field(default_factory=dict)

    @property
    def risk_pips(self) -> float:
        return abs(self.entry_price - self.stop_loss) / config.PIP_VALUE_JPY

    @property
    def reward_pips(self) -> float:
        return abs(self.take_profit - self.entry_price) / config.PIP_VALUE_JPY


# ── Time Filter ────────────────────────────────────────────────────────────────

def is_trading_hours(dt: Optional[datetime] = None) -> bool:
    """
    Returns True when we are allowed to trade (not in the no-trade window).

    No-trade: 22:00–07:00 JST  →  13:00–22:00 UTC
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    # No-trade window: 13 <= hour < 22 UTC
    if config.NO_TRADE_START_UTC <= hour < config.NO_TRADE_END_UTC:
        logger.debug("Outside trading hours (UTC %02d:xx). Skipping.", hour)
        return False
    return True


# ── Range Detection ────────────────────────────────────────────────────────────

def is_range_market(indicators: dict) -> bool:
    """
    True when the market is ranging (no clear trend in wave + flat 3MA).
    Range mode requires a different approach; we skip normal signals.
    """
    wave_range = indicators["wave_trend"] == "range"
    ma_flat = indicators["ma_direction"] == "flat"
    return wave_range or ma_flat


# ── Entry Conditions ───────────────────────────────────────────────────────────

def _long_conditions(ind: dict) -> tuple[bool, list[str]]:
    """
    Check all three entry conditions for a LONG setup.
    Returns (all_met: bool, reasons: list[str]).
    """
    passed = []
    failed = []

    # ① 3MA: price above rising MA
    if ind["ma_direction"] == "up" and ind["price_vs_ma"] == "above":
        passed.append("3MA上昇・価格MA上")
    else:
        failed.append(f"3MA条件NG(方向={ind['ma_direction']}, 価格={ind['price_vs_ma']})")

    # ② Wave: uptrend (higher highs + higher lows)
    if ind["wave_trend"] == "uptrend":
        passed.append("波形=上昇トレンド(HH+HL)")
    else:
        failed.append(f"波形NG({ind['wave_trend']})")

    # ③ RSI: bullish (> 50) or recovering from oversold
    rsi_ok = ind["rsi_signal"] in ("bullish", "oversold") or (
        ind["rsi_signal"] == "neutral" and ind["rsi"] > 50
    )
    if rsi_ok:
        passed.append(f"RSI={ind['rsi']:.1f}(強気)")
    else:
        failed.append(f"RSI条件NG({ind['rsi']:.1f}, {ind['rsi_signal']})")

    all_met = len(failed) == 0
    reasons = passed if all_met else failed
    return all_met, reasons


def _short_conditions(ind: dict) -> tuple[bool, list[str]]:
    """
    Check all three entry conditions for a SHORT setup.
    """
    passed = []
    failed = []

    # ① 3MA: price below falling MA
    if ind["ma_direction"] == "down" and ind["price_vs_ma"] == "below":
        passed.append("3MA下降・価格MA下")
    else:
        failed.append(f"3MA条件NG(方向={ind['ma_direction']}, 価格={ind['price_vs_ma']})")

    # ② Wave: downtrend (lower highs + lower lows)
    if ind["wave_trend"] == "downtrend":
        passed.append("波形=下降トレンド(LH+LL)")
    else:
        failed.append(f"波形NG({ind['wave_trend']})")

    # ③ RSI: bearish (< 50) or coming from overbought
    rsi_ok = ind["rsi_signal"] in ("bearish", "overbought") or (
        ind["rsi_signal"] == "neutral" and ind["rsi"] < 50
    )
    if rsi_ok:
        passed.append(f"RSI={ind['rsi']:.1f}(弱気)")
    else:
        failed.append(f"RSI条件NG({ind['rsi']:.1f}, {ind['rsi_signal']})")

    all_met = len(failed) == 0
    reasons = passed if all_met else failed
    return all_met, reasons


# ── Stop Loss / Take Profit Calculation ───────────────────────────────────────

def _calc_levels(signal: Signal, entry: float, ind: dict) -> tuple[float, float]:
    """
    Calculate stop-loss and take-profit prices.

    Stop loss is placed:
      LONG  : below the last swing low  (or 20 pips below entry, whichever is wider)
      SHORT : above the last swing high (or 20 pips above entry, whichever is wider)

    Take profit: 2× the actual stop distance (minimum 2:1 R/R).
    """
    pip = config.PIP_VALUE_JPY
    fixed_sl_pips = config.STOP_LOSS_PIPS * pip

    if signal == Signal.LONG:
        swing_sl = ind.get("last_swing_low")
        if swing_sl and (entry - swing_sl) > fixed_sl_pips:
            sl = swing_sl - pip * 2  # 2 pip buffer below swing low
        else:
            sl = entry - fixed_sl_pips
        risk = entry - sl
        tp = entry + risk * 2  # 2:1

    else:  # SHORT
        swing_sl = ind.get("last_swing_high")
        if swing_sl and (swing_sl - entry) > fixed_sl_pips:
            sl = swing_sl + pip * 2  # 2 pip buffer above swing high
        else:
            sl = entry + fixed_sl_pips
        risk = sl - entry
        tp = entry - risk * 2  # 2:1

    return round(sl, 3), round(tp, 3)


# ── Main Strategy Evaluation ──────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, now: Optional[datetime] = None) -> TradeSetup:
    """
    Evaluate the current market state and return a TradeSetup.

    Parameters
    ----------
    df  : OHLC DataFrame (columns: open, high, low, close)
    now : current UTC datetime (defaults to utcnow)

    Returns
    -------
    TradeSetup with signal=NONE if no trade, or LONG/SHORT with levels.
    """
    no_trade = TradeSetup(
        signal=Signal.NONE,
        entry_price=0.0,
        stop_loss=0.0,
        take_profit=0.0,
        reason="",
    )

    # ── Guard: minimum data ────────────────────────────────────────────────────
    if df is None or len(df) < 30:
        no_trade.reason = "データ不足"
        return no_trade

    # ── Rule: no night entries ─────────────────────────────────────────────────
    if not is_trading_hours(now):
        no_trade.reason = "夜中のためスキップ"
        return no_trade

    # ── Compute indicators ─────────────────────────────────────────────────────
    ind = compute_all_indicators(
        df,
        ma_period=config.MA_PERIOD,
        rsi_period=config.RSI_PERIOD,
        wave_lookback=config.WAVE_LOOKBACK,
    )

    logger.info(
        "Indicators: close=%.3f  3MA=%.3f(%s)  RSI=%.1f(%s)  Wave=%s  Channel=%s",
        ind["close"], ind["ma3"], ind["ma_direction"],
        ind["rsi"], ind["rsi_signal"],
        ind["wave_trend"], ind["channel_type"],
    )

    # ── Rule: range market → skip ──────────────────────────────────────────────
    if is_range_market(ind):
        no_trade.reason = f"レンジ相場のためスキップ(wave={ind['wave_trend']}, MA={ind['ma_direction']})"
        no_trade.indicators = ind
        return no_trade

    entry = ind["close"]

    # ── Evaluate LONG ──────────────────────────────────────────────────────────
    long_ok, long_reasons = _long_conditions(ind)
    if long_ok:
        sl, tp = _calc_levels(Signal.LONG, entry, ind)
        reason = "【買いエントリー】 " + " / ".join(long_reasons)
        logger.info(reason)
        return TradeSetup(
            signal=Signal.LONG,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            reason=reason,
            indicators=ind,
        )

    # ── Evaluate SHORT ─────────────────────────────────────────────────────────
    short_ok, short_reasons = _short_conditions(ind)
    if short_ok:
        sl, tp = _calc_levels(Signal.SHORT, entry, ind)
        reason = "【売りエントリー】 " + " / ".join(short_reasons)
        logger.info(reason)
        return TradeSetup(
            signal=Signal.SHORT,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            reason=reason,
            indicators=ind,
        )

    # ── No signal ─────────────────────────────────────────────────────────────
    failed_reasons = long_reasons + short_reasons
    no_trade.reason = "条件未達: " + " | ".join(failed_reasons[:4])
    no_trade.indicators = ind
    logger.debug("No signal. %s", no_trade.reason)
    return no_trade
