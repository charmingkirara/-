"""
Trade Manager
────────────────────────────────────────────────────────────────────────────────
Implements active position management after entry:

  Rule: 利確SLは、20伸びた後に1分足で判定。
  ─────────────────────────────────────────
  Once a trade has moved EXTENSION_TRIGGER_PIPS (20 pips) in profit,
  switch to 1-minute chart monitoring:
    - Compute 3MA direction on M1
    - Compute RSI on M1
    - If M1 signals a reversal (MA turns against the trade direction OR
      RSI crosses back through 50), close the trade immediately.

  This prevents giving back large gains while allowing the trade to run
  when momentum is still strong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from trading_bot import config
from trading_bot.indicators import (
    calculate_ma,
    calculate_rsi,
    ma_direction,
)
from trading_bot.strategy import Signal

logger = logging.getLogger(__name__)


# ── Data Structures ────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    """State of a currently open trade."""
    trade_id: str
    signal: Signal
    entry_price: float
    stop_loss: float
    take_profit: float
    units: int


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str
    current_profit_pips: float


# ── Profit Calculation ─────────────────────────────────────────────────────────

def current_profit_pips(position: OpenPosition, current_price: float) -> float:
    """Unrealised P&L in pips for the given position."""
    pip = config.PIP_VALUE_JPY
    if position.signal == Signal.LONG:
        return (current_price - position.entry_price) / pip
    else:
        return (position.entry_price - current_price) / pip


# ── 1-Minute Exit Logic ────────────────────────────────────────────────────────

def _m1_reversal(df_m1: pd.DataFrame, signal: Signal) -> tuple[bool, str]:
    """
    Check the 1-minute chart for a reversal signal against our open direction.

    Long trade reversal:
      - 3MA on M1 turns DOWN  (direction == 'down')
      - AND RSI drops below 50

    Short trade reversal:
      - 3MA on M1 turns UP    (direction == 'up')
      - AND RSI rises above 50
    """
    if df_m1 is None or len(df_m1) < config.M1_EXIT_MA_PERIOD + config.M1_EXIT_RSI_PERIOD:
        return False, "M1データ不足"

    close = df_m1["close"]
    ma = calculate_ma(close, config.M1_EXIT_MA_PERIOD)
    rsi = calculate_rsi(close, config.M1_EXIT_RSI_PERIOD)

    direction = ma_direction(ma, lookback=3)
    latest_rsi = float(rsi.iloc[-1])
    latest_close = float(close.iloc[-1])
    latest_ma = float(ma.iloc[-1]) if not ma.empty else latest_close

    if signal == Signal.LONG:
        ma_reversed = direction == "down"
        rsi_reversed = latest_rsi < 50
        price_below_ma = latest_close < latest_ma

        if ma_reversed and (rsi_reversed or price_below_ma):
            return True, f"1分足反転シグナル(ロング→売転換): MA={direction} RSI={latest_rsi:.1f}"

    else:  # SHORT
        ma_reversed = direction == "up"
        rsi_reversed = latest_rsi > 50
        price_above_ma = latest_close > latest_ma

        if ma_reversed and (rsi_reversed or price_above_ma):
            return True, f"1分足反転シグナル(ショート→買転換): MA={direction} RSI={latest_rsi:.1f}"

    return False, f"1分足継続中: MA={direction} RSI={latest_rsi:.1f}"


# ── Main Exit Evaluation ───────────────────────────────────────────────────────

def evaluate_exit(
    position: OpenPosition,
    current_price: float,
    broker,
) -> ExitDecision:
    """
    Decide whether to close the open position.

    Phase 1 – Normal SL/TP:
      The broker's attached SL/TP orders handle this automatically.
      No manual intervention needed.

    Phase 2 – After 20 pip extension:
      Switch to 1-minute chart monitoring.
      Close if M1 shows a reversal signal.

    Parameters
    ----------
    position      : OpenPosition
    current_price : latest bid/ask midpoint
    broker        : OANDABroker instance

    Returns
    -------
    ExitDecision with should_exit=True if we should close now.
    """
    profit = current_profit_pips(position, current_price)

    # ── Phase 1: trade not yet extended 20 pips ───────────────────────────────
    if profit < config.EXTENSION_TRIGGER_PIPS:
        return ExitDecision(
            should_exit=False,
            reason=f"通常管理中 (+{profit:.1f}pips < {config.EXTENSION_TRIGGER_PIPS}pips)",
            current_profit_pips=profit,
        )

    # ── Phase 2: 20 pips in profit → check M1 chart ───────────────────────────
    logger.info(
        "Trade %s reached +%.1f pips. Switching to M1 exit monitoring.",
        position.trade_id, profit,
    )

    df_m1 = broker.fetch_candles(
        granularity=config.TF_M1,
        count=config.M1_EXIT_CANDLE_COUNT,
    )

    should_exit, reason = _m1_reversal(df_m1, position.signal)

    return ExitDecision(
        should_exit=should_exit,
        reason=reason,
        current_profit_pips=profit,
    )


# ── Position Registry ──────────────────────────────────────────────────────────

class PositionRegistry:
    """
    Simple in-memory registry of open positions.
    Maps trade_id -> OpenPosition.
    """

    def __init__(self) -> None:
        self._positions: dict[str, OpenPosition] = {}

    def add(self, position: OpenPosition) -> None:
        self._positions[position.trade_id] = position
        logger.info("Position registered: %s %s @ %.3f",
                    position.signal.value, position.trade_id, position.entry_price)

    def remove(self, trade_id: str) -> None:
        if trade_id in self._positions:
            del self._positions[trade_id]
            logger.info("Position removed: %s", trade_id)

    def all(self) -> list[OpenPosition]:
        return list(self._positions.values())

    def __len__(self) -> int:
        return len(self._positions)

    def __contains__(self, trade_id: str) -> bool:
        return trade_id in self._positions
