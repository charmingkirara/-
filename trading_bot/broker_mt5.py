"""
MetaTrader 5 broker integration.

Handles:
  - Connecting to MT5 terminal
  - Fetching OHLC candles
  - Placing market orders with stop loss / take profit
  - Fetching open trades (positions)
  - Closing trades

NOTE: MetaTrader5 Python library is Windows-only.
      Run this bot on the same machine as your MT5 terminal.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from trading_bot import config
from trading_bot.strategy import Signal, TradeSetup

logger = logging.getLogger(__name__)

# Lazy import so the module can be imported on non-Windows for type-checking
try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    mt5 = None  # type: ignore


# ── Granularity mapping ────────────────────────────────────────────────────────

_GRANULARITY_MAP: dict[str, int] = {}


def _build_granularity_map() -> dict[str, int]:
    """Build TF string → MT5 timeframe constant mapping."""
    if not _MT5_AVAILABLE:
        return {}
    return {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
        "W1":  mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    }


class MT5Broker:
    """
    MT5 broker implementation with the same interface as OANDABroker.
    All strategy / indicator / trade_manager code works unchanged.
    """

    def __init__(self) -> None:
        if not _MT5_AVAILABLE:
            raise RuntimeError(
                "MetaTrader5 package is not installed. "
                "Install it with: pip install MetaTrader5"
            )

        self._symbol = config.MT5_SYMBOL
        self._magic = config.MT5_MAGIC_NUMBER
        self._deviation = config.MT5_DEVIATION
        self._lot = config.MT5_LOT_SIZE

        global _GRANULARITY_MAP
        _GRANULARITY_MAP = _build_granularity_map()

        self._connect()

    # ── Connection ─────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        """Initialize and log in to the MT5 terminal."""
        if not mt5.initialize(
            login=config.MT5_LOGIN,
            password=config.MT5_PASSWORD,
            server=config.MT5_SERVER,
        ):
            error = mt5.last_error()
            raise ConnectionError(f"MT5 initialize failed: {error}")

        account = mt5.account_info()
        if account is None:
            raise ConnectionError(f"MT5 account_info failed: {mt5.last_error()}")

        logger.info(
            "MT5 connected: account=%d  server=%s  balance=%.2f",
            account.login, account.server, account.balance,
        )

    def shutdown(self) -> None:
        """Disconnect from the MT5 terminal."""
        mt5.shutdown()
        logger.info("MT5 disconnected.")

    # ── Market Data ────────────────────────────────────────────────────────────

    def fetch_candles(
        self,
        instrument: str = config.MT5_SYMBOL,
        granularity: str = config.GRANULARITY,
        count: int = config.CANDLE_COUNT,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLC candles from MT5.
        Returns a DataFrame with columns: open, high, low, close, volume
        indexed by time (UTC). Returns None on error.

        Note: `instrument` parameter is accepted for API compatibility but
        MT5Broker always uses config.MT5_SYMBOL internally.
        """
        tf = _GRANULARITY_MAP.get(granularity)
        if tf is None:
            logger.error("Unknown granularity: %s", granularity)
            return None

        rates = mt5.copy_rates_from_pos(self._symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.error(
                "MT5 copy_rates_from_pos failed for %s %s: %s",
                self._symbol, granularity, mt5.last_error(),
            )
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        df = df.rename(columns={
            "open":  "open",
            "high":  "high",
            "low":   "low",
            "close": "close",
            "tick_volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]]

        # Drop the latest (potentially incomplete) bar
        if len(df) > 1:
            df = df.iloc[:-1]

        return df

    # ── Account ────────────────────────────────────────────────────────────────

    def get_account_balance(self) -> Optional[float]:
        """Return current account balance."""
        info = mt5.account_info()
        if info is None:
            logger.error("MT5 account_info failed: %s", mt5.last_error())
            return None
        return float(info.balance)

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_order(
        self,
        setup: TradeSetup,
        units: int = config.UNITS,
    ) -> Optional[str]:
        """
        Place a market order based on the TradeSetup.

        Returns the position ticket (as string) if successful, None on failure.
        Units are ignored; lot size comes from config.MT5_LOT_SIZE.
        """
        if setup.signal == Signal.NONE:
            logger.warning("place_order called with NONE signal – skipping")
            return None

        order_type = (
            mt5.ORDER_TYPE_BUY
            if setup.signal == Signal.LONG
            else mt5.ORDER_TYPE_SELL
        )

        symbol_info = mt5.symbol_info(self._symbol)
        if symbol_info is None:
            logger.error("MT5 symbol_info failed for %s", self._symbol)
            return None

        point = symbol_info.point
        digits = symbol_info.digits

        sl = round(setup.stop_loss, digits)
        tp = round(setup.take_profit, digits)

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    self._symbol,
            "volume":    self._lot,
            "type":      order_type,
            "sl":        sl,
            "tp":        tp,
            "deviation": self._deviation,
            "magic":     self._magic,
            "comment":   f"bot_{setup.entry_type}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            logger.error("MT5 order_send returned None: %s", mt5.last_error())
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                "MT5 order failed. retcode=%d comment=%s",
                result.retcode, result.comment,
            )
            return None

        ticket = str(result.order)
        logger.info(
            "Order placed. Signal=%s  entry=%.3f  SL=%.3f  TP=%.3f  ticket=%s",
            setup.signal.value, setup.entry_price, sl, tp, ticket,
        )
        return ticket

    # ── Open Trades ────────────────────────────────────────────────────────────

    def get_open_trades(self) -> list[dict]:
        """
        Return a list of open position dicts for the configured symbol.
        Each dict has at minimum: {"id": ticket_str}
        """
        positions = mt5.positions_get(symbol=self._symbol)
        if positions is None:
            logger.error("MT5 positions_get failed: %s", mt5.last_error())
            return []

        result = []
        for pos in positions:
            if pos.magic != self._magic:
                continue  # Skip positions opened by other bots/manually
            result.append({
                "id":     str(pos.ticket),
                "symbol": pos.symbol,
                "type":   "LONG" if pos.type == mt5.POSITION_TYPE_BUY else "SHORT",
                "volume": pos.volume,
                "price_open": pos.price_open,
                "sl":    pos.sl,
                "tp":    pos.tp,
                "profit": pos.profit,
            })
        return result

    def has_open_trade(self) -> bool:
        """True if there is already an open position in the symbol."""
        return len(self.get_open_trades()) > 0

    def close_trade(self, trade_id: str) -> bool:
        """
        Close a specific position by ticket ID.
        Returns True on success.
        """
        ticket = int(trade_id)
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning("No open position found for ticket %s", trade_id)
            return False

        pos = positions[0]
        # Opposite order type to close
        close_type = (
            mt5.ORDER_TYPE_SELL
            if pos.type == mt5.POSITION_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )

        symbol_info = mt5.symbol_info(self._symbol)
        close_price = (
            mt5.symbol_info_tick(self._symbol).bid
            if close_type == mt5.ORDER_TYPE_SELL
            else mt5.symbol_info_tick(self._symbol).ask
        )

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    self._symbol,
            "volume":    pos.volume,
            "type":      close_type,
            "position":  ticket,
            "price":     close_price,
            "deviation": self._deviation,
            "magic":     self._magic,
            "comment":   "bot_close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            comment = result.comment if result else mt5.last_error()
            logger.error(
                "MT5 close failed for ticket %s. retcode=%s comment=%s",
                trade_id, retcode, comment,
            )
            return False

        logger.info("Trade %s closed.", trade_id)
        return True

    def close_all_trades(self) -> int:
        """Close all open positions for the symbol. Returns number closed."""
        open_trades = self.get_open_trades()
        closed = 0
        for trade in open_trades:
            if self.close_trade(trade["id"]):
                closed += 1
        return closed
