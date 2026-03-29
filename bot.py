"""
USD/JPY 15分足 自動売買ボット
────────────────────────────────────────────────────────────────────────────────
戦略:
  エントリー条件（3つ全て揃ったところ）:
    ① 3MA  : 価格がMAの上（ロング）/ 下（ショート）
    ② 波形  : 高値・安値の更新方向（上昇トレンド / 下降トレンド）
    ③ RSI  : 50超（ロング）/ 50未満（ショート）

徹底事項:
    - 夜中はエントリーしない（22:00–07:00 JST）
    - シグナルだけで入らない（3条件必須）
    - 損切りの根拠を持たせる（20pips / 直近スイング）

Usage:
    python bot.py [--dry-run]

    --dry-run : シグナル確認のみ・注文は出さない
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from trading_bot import config
from trading_bot.broker import OANDABroker
from trading_bot.strategy import Signal, evaluate

# ── Logging Setup ──────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        ],
    )


logger = logging.getLogger("bot")


# ── Single Cycle ──────────────────────────────────────────────────────────────

def run_cycle(broker: OANDABroker, dry_run: bool = False) -> None:
    """Execute one evaluation cycle."""
    now = datetime.now(timezone.utc)
    logger.info("─── Cycle start: %s ───", now.strftime("%Y-%m-%d %H:%M UTC"))

    # 1. Fetch candles
    df = broker.fetch_candles()
    if df is None or df.empty:
        logger.warning("Failed to fetch candles. Skipping cycle.")
        return

    # 2. Evaluate strategy
    setup = evaluate(df, now=now)

    logger.info(
        "Signal: %-5s  Reason: %s",
        setup.signal.value, setup.reason,
    )

    if setup.signal == Signal.NONE:
        return

    # Log the full setup details
    logger.info(
        "  Entry=%.3f  SL=%.3f (%.1f pips)  TP=%.3f (%.1f pips)",
        setup.entry_price,
        setup.stop_loss, setup.risk_pips,
        setup.take_profit, setup.reward_pips,
    )

    if setup.indicators:
        ind = setup.indicators
        logger.info(
            "  3MA=%.3f(%s)  RSI=%.1f(%s)  Wave=%s  Channel=%s",
            ind["ma3"], ind["ma_direction"],
            ind["rsi"], ind["rsi_signal"],
            ind["wave_trend"], ind["channel_type"],
        )

    # 3. Guard: no double entry
    if broker.has_open_trade():
        logger.info("Already have an open trade. Skipping new entry.")
        return

    # 4. Place order (or dry-run)
    if dry_run:
        logger.info("[DRY-RUN] Would place %s order. No actual order sent.", setup.signal.value)
        return

    trade_id = broker.place_order(setup)
    if trade_id:
        logger.info("Trade opened. ID=%s", trade_id)
    else:
        logger.error("Order placement failed.")


# ── Main Loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="USD/JPY 15分足 自動売買ボット")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="シグナル確認のみ。実際の注文は出しません。",
    )
    args = parser.parse_args()

    _setup_logging()
    logger.info("=" * 60)
    logger.info("USD/JPY 自動売買ボット 起動")
    logger.info("モード: %s", "DRY-RUN" if args.dry_run else "LIVE")
    logger.info("=" * 60)

    if not config.OANDA_API_KEY or not config.OANDA_ACCOUNT_ID:
        logger.error("OANDA_API_KEY / OANDA_ACCOUNT_ID が設定されていません。.envを確認してください。")
        sys.exit(1)

    broker = OANDABroker()

    balance = broker.get_account_balance()
    if balance is not None:
        logger.info("口座残高: %.2f", balance)

    logger.info("ポーリング間隔: %d秒", config.POLL_INTERVAL_SECONDS)
    logger.info("取引不可時間帯 (UTC): %02d:00 – %02d:00", config.NO_TRADE_START_UTC, config.NO_TRADE_END_UTC)

    try:
        while True:
            try:
                run_cycle(broker, dry_run=args.dry_run)
            except Exception as exc:
                logger.exception("Cycle error (continuing): %s", exc)

            time.sleep(config.POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("ボット停止 (Ctrl+C)")


if __name__ == "__main__":
    main()
