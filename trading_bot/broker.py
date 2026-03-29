"""
OANDA v20 REST API broker integration.

Handles:
  - Fetching OHLC candles
  - Placing market orders with stop loss / take profit
  - Fetching open trades
  - Closing trades
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades_ep
import oandapyV20.endpoints.accounts as accounts

from trading_bot import config
from trading_bot.strategy import Signal, TradeSetup

logger = logging.getLogger(__name__)


class OANDABroker:
    """Thin wrapper around the OANDA v20 REST API."""

    def __init__(self):
        environment = config.OANDA_ENVIRONMENT  # "practice" or "live"
        self._client = oandapyV20.API(
            access_token=config.OANDA_API_KEY,
            environment=environment,
        )
        self._account_id = config.OANDA_ACCOUNT_ID

    # ── Market Data ────────────────────────────────────────────────────────────

    def fetch_candles(
        self,
        instrument: str = config.INSTRUMENT,
        granularity: str = config.GRANULARITY,
        count: int = config.CANDLE_COUNT,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLC candles from OANDA.
        Returns a DataFrame with columns: time, open, high, low, close, volume.
        Returns None on error.
        """
        params = {
            "granularity": granularity,
            "count": count,
            "price": "M",  # midpoint prices
        }
        request = instruments.InstrumentsCandles(instrument, params=params)
        try:
            self._client.request(request)
        except oandapyV20.exceptions.V20Error as exc:
            logger.error("OANDA candles error: %s", exc)
            return None

        candles = request.response.get("candles", [])
        if not candles:
            logger.warning("No candles returned for %s", instrument)
            return None

        rows = []
        for c in candles:
            if not c.get("complete", False):
                continue
            mid = c["mid"]
            rows.append({
                "time": pd.Timestamp(c["time"]),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c.get("volume", 0)),
            })

        if not rows:
            logger.warning("All candles were incomplete for %s", instrument)
            return None

        df = pd.DataFrame(rows).set_index("time")
        return df

    # ── Account ────────────────────────────────────────────────────────────────

    def get_account_balance(self) -> Optional[float]:
        """Return current account balance."""
        request = accounts.AccountDetails(self._account_id)
        try:
            self._client.request(request)
            balance = float(request.response["account"]["balance"])
            return balance
        except oandapyV20.exceptions.V20Error as exc:
            logger.error("OANDA account error: %s", exc)
            return None

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_order(self, setup: TradeSetup, units: int = config.UNITS) -> Optional[str]:
        """
        Place a market order based on the TradeSetup.

        Parameters
        ----------
        setup : TradeSetup from strategy.evaluate()
        units : position size (negative for short)

        Returns the trade ID string if successful, None on failure.
        """
        if setup.signal == Signal.NONE:
            logger.warning("place_order called with NONE signal – skipping")
            return None

        direction_units = units if setup.signal == Signal.LONG else -units

        order_body = {
            "order": {
                "type": "MARKET",
                "instrument": config.INSTRUMENT,
                "units": str(direction_units),
                "stopLossOnFill": {
                    "price": f"{setup.stop_loss:.3f}",
                    "timeInForce": "GTC",
                },
                "takeProfitOnFill": {
                    "price": f"{setup.take_profit:.3f}",
                    "timeInForce": "GTC",
                },
            }
        }

        request = orders.OrderCreate(self._account_id, data=order_body)
        try:
            self._client.request(request)
        except oandapyV20.exceptions.V20Error as exc:
            logger.error("OANDA order error: %s", exc)
            return None

        response = request.response
        trade_id = None

        # Order filled immediately
        if "orderFillTransaction" in response:
            trade_id = response["orderFillTransaction"].get("tradeOpened", {}).get("tradeID")

        if trade_id:
            logger.info(
                "Order placed. Signal=%s  entry=%.3f  SL=%.3f  TP=%.3f  trade_id=%s",
                setup.signal.value, setup.entry_price,
                setup.stop_loss, setup.take_profit, trade_id,
            )
        else:
            logger.warning("Order placed but no tradeID returned. Response: %s", response)

        return trade_id

    # ── Open Trades ────────────────────────────────────────────────────────────

    def get_open_trades(self) -> list[dict]:
        """Return a list of open trade dicts for the configured instrument."""
        request = trades_ep.TradesList(
            self._account_id,
            params={"instrument": config.INSTRUMENT, "state": "OPEN"},
        )
        try:
            self._client.request(request)
            return request.response.get("trades", [])
        except oandapyV20.exceptions.V20Error as exc:
            logger.error("OANDA trades list error: %s", exc)
            return []

    def has_open_trade(self) -> bool:
        """True if there is already an open position in USD_JPY."""
        return len(self.get_open_trades()) > 0

    def close_trade(self, trade_id: str) -> bool:
        """Close a specific trade by ID. Returns True on success."""
        request = trades_ep.TradeClose(self._account_id, tradeID=trade_id)
        try:
            self._client.request(request)
            logger.info("Trade %s closed.", trade_id)
            return True
        except oandapyV20.exceptions.V20Error as exc:
            logger.error("OANDA close error for trade %s: %s", trade_id, exc)
            return False

    def close_all_trades(self) -> int:
        """Close all open USD_JPY trades. Returns number closed."""
        open_trades = self.get_open_trades()
        closed = 0
        for trade in open_trades:
            if self.close_trade(trade["id"]):
                closed += 1
        return closed
