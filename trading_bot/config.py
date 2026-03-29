"""
Trading bot configuration.
Implements the USD/JPY multi-timeframe strategy:
  Entry requires all 3 conditions: 3MA direction + wave pattern + RSI
  HTF (H1/H4) trend filter must align
  Stop loss: 20 pips
  No night entries (22:00-07:00 JST = 13:00-22:00 UTC)
  飛び乗り禁止: no chasing entries
  利確SL: after 20 pip extension, use 1-min chart to exit
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
GRANULARITY = "M15"          # Primary entry timeframe: 15-minute candles
CANDLE_COUNT = 100           # Candles to fetch per cycle

# ── Multi-Timeframe Granularities ──────────────────────────────────────────────
# Used for HTF trend filter and pullback level detection
TF_M1  = "M1"    # 1-minute  : 利確SL判定（20pips伸びた後）
TF_M5  = "M5"    # 5-minute  : 5分戻り高値/押安値
TF_M15 = "M15"   # 15-minute : 15分戻り高値/押安値 (primary)
TF_H1  = "H1"    # 1-hour    : 1時間戻り高値/押安値 + HTFトレンド
TF_H4  = "H4"    # 4-hour    : 4時間足トレンドフィルター
TF_D1  = "D1"    # Daily     : 大局確認

HTF_CANDLE_COUNT = 60        # Bars to fetch for higher timeframes

# ── Indicator Parameters ───────────────────────────────────────────────────────
MA_PERIOD = 3                # 3MA (3-period moving average) — primary trend MA
SMA_SHORT = 5                # 短期SMA (signal: short-term SMA crosses medium)
SMA_MID   = 20               # 中期SMA (trend confirmation with 50MA)
SMA_LONG  = 50               # 長期SMA (trend confirmation, both slope = trend)
RSI_PERIOD = 14              # RSI period
RSI_UPPER = 70               # RSI overbought — caution on M5, avoid entry on M15
RSI_LOWER = 30               # RSI oversold  — caution on M5, avoid entry on M15
RSI_MID = 50                 # RSI midline (long > 50, short < 50)

# ── Wave Detection ─────────────────────────────────────────────────────────────
WAVE_LOOKBACK = 5            # Candles for swing high/low detection

# ── Range Detection ────────────────────────────────────────────────────────────
# Range判定: 高値も安値も更新しない、3MAが中央帯付近に集まっている
RANGE_MA_CLUSTER_PIPS = 10   # If 3MA, 20MA, 50MA are within this range → ranging
RANGE_BREAKOUT_CANDLES = 2   # Bars outside range to confirm breakout

# ── Risk Management ────────────────────────────────────────────────────────────
STOP_LOSS_PIPS = 20          # Minimum SL distance (エントリー根拠が崩れた箇所)
TAKE_PROFIT_PIPS = 40        # Initial TP target (20pips伸びたら建値→その後30~40pips)
PIP_VALUE_JPY = 0.01         # 1 pip = 0.01 for JPY pairs (USD/JPY)
UNITS = 1000                 # Trade size (adjust to your account)

# TP/SL spread buffer: TP/SLもスプレッド分を引いておく
SPREAD_BUFFER_PIPS = 5       # 余裕があれば5~10pips。最低2.5pips
MIN_RISK_REWARD = 1.0        # Minimum R:R ratio (1:1 ~ 1:2)

# ── Breakeven / Trailing ────────────────────────────────────────────────────────
# シグナル発生点から20pips以上伸びたら建値を検討、その後さらに10~20pips
BREAKEVEN_TRIGGER_PIPS = 20  # Move SL to breakeven after this profit
TRAIL_ADDITIONAL_PIPS  = 15  # Trail SL by this amount after breakeven

# ── 利確SL管理 (Trade Management) ──────────────────────────────────────────────
# 利確SLは、20伸びた後に1分足で判定。
EXTENSION_TRIGGER_PIPS = 20  # Pips of profit before switching to 1-min exit logic
M1_EXIT_MA_PERIOD = 3        # 3MA on M1 for exit signal
M1_EXIT_RSI_PERIOD = 14      # RSI on M1 for exit confirmation
M1_EXIT_CANDLE_COUNT = 30    # Bars of M1 data to fetch for exit analysis

# ── Anti-Chasing Filter (飛び乗り禁止) ────────────────────────────────────────
# 飛び乗りは絶対ダメ！
# If price has moved more than this many pips away from the 3MA at signal time,
# the setup is "stale" and entry is skipped.
MAX_ENTRY_DISTANCE_FROM_MA_PIPS = 8   # Max pips price can be from 3MA to enter
MAX_CANDLES_SINCE_SIGNAL = 3          # Signal is stale after this many M15 bars

# ── Time Filter (no night entries) ─────────────────────────────────────────────
# 夜中はエントリーしない: avoid 22:00-07:00 JST
# JST = UTC+9, so no-trade window in UTC: 13:00-22:00
NO_TRADE_START_UTC = 13      # 22:00 JST
NO_TRADE_END_UTC = 22        # 07:00 JST

# ── Bot Timing ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 60   # Check every 60 seconds

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE = "trading_bot.log"
