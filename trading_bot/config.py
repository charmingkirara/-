"""
Trading bot configuration.
Implements the USD/JPY 15-minute strategy:
  Entry requires all 3 conditions: 3MA direction + wave pattern + RSI
  Stop loss: 20 pips
  No night entries (22:00-07:00 JST = 13:00-22:00 UTC)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── OANDA Credentials ──────────────────────────────────────────────────────────
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")  # "practice" or "live"

# ── Instrument ─────────────────────────────────────────────────────────────────
INSTRUMENT = "USD_JPY"
GRANULARITY = "M15"          # 15-minute candles
CANDLE_COUNT = 100           # Candles to fetch per cycle

# ── Indicator Parameters ───────────────────────────────────────────────────────
MA_PERIOD = 3                # 3MA (3-period moving average)
RSI_PERIOD = 14              # RSI period
RSI_UPPER = 70               # RSI overbought threshold
RSI_LOWER = 30               # RSI oversold threshold
RSI_MID = 50                 # RSI midline (long > 50, short < 50)

# ── Wave Detection ─────────────────────────────────────────────────────────────
# Number of candles to look back for swing high/low detection
WAVE_LOOKBACK = 5

# ── Risk Management ────────────────────────────────────────────────────────────
STOP_LOSS_PIPS = 20          # 20 pip stop loss (徹底事項: 損切りの根拠を持たせる)
TAKE_PROFIT_PIPS = 40        # 2:1 reward/risk
PIP_VALUE_JPY = 0.01         # 1 pip = 0.01 for JPY pairs
UNITS = 1000                 # Trade size (adjust to your account)

# ── Time Filter (no night entries) ─────────────────────────────────────────────
# 夜中はエントリーしない: avoid 22:00-07:00 JST
# JST = UTC+9, so no-trade window in UTC: 13:00-22:00
NO_TRADE_START_UTC = 13      # 22:00 JST
NO_TRADE_END_UTC = 22        # 07:00 JST

# ── Bot Timing ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 60   # Check every 60 seconds

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE = "trading_bot.log"
