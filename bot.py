"""
USD/JPY 自動売買ボット
────────────────────────────────────────────────────────────────────────────────
トレードルーティン:
  ① 環境認識（HTFで水平線・レジサポ特定）
  ② トレンド/レンジ判定（5分足MA + 波形）
  ③ エントリー（3条件揃ったところ）:
       トレンド: 押し・戻しのPO（SMA5×SMA20）で順張り
       レンジ : (a)ブレイクアウト  (b)上限下限逆張り
  ④ TP/SL管理:
       20pips伸びたら建値検討 → 1分足で継続確認 → 30~40pips目標
  ⑤ 禁則事項:
       - 深夜（01:00–08:00 JST = 16:00–23:00 UTC）は静観
       - 飛び乗り禁止（価格がMAから離れすぎ）
       - 方向感ゼロのときは静観

Usage:
    python bot.py [--dry-run]
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
from trading_bot.strategy import Signal, MarketState, evaluate


def _create_broker(broker_name: str):
    """Instantiate the appropriate broker based on name."""
    if broker_name == "mt5":
        from trading_bot.broker_mt5 import MT5Broker
        return MT5Broker()
    else:
        return OANDABroker()
from trading_bot.timeframes import build_mtf_context
from trading_bot.trade_manager import (
    OpenPosition,
    PositionRegistry,
    evaluate_exit,
)

# ── Logging ────────────────────────────────────────────────────────────────────

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

# Global position registry (persists across cycles within a process run)
registry = PositionRegistry()

# Cooldown after SL hit: wait this many seconds before next entry
_COOLDOWN_AFTER_SL_SECONDS = 60 * 45  # 45 minutes (3 M15 bars)
_last_sl_hit_time: float = 0.0  # Unix timestamp


# ── Trade Management Cycle ─────────────────────────────────────────────────────

def manage_open_positions(broker: OANDABroker, dry_run: bool) -> None:
    """
    ポジション管理サイクル。
    利確SLは、20pips伸びた後に1分足で判定。
    """
    if len(registry) == 0:
        return

    # Sync registry against broker open trades
    open_from_broker = {t["id"] for t in broker.get_open_trades()}

    for pos in registry.all():
        # Trade was closed externally (SL/TP hit)
        if pos.trade_id not in open_from_broker:
            profit = (float(pos.take_profit) - float(pos.entry_price)) if pos.signal.value == "LONG" else (float(pos.entry_price) - float(pos.take_profit))
            # Detect SL hit: position closed before TP
            global _last_sl_hit_time
            _last_sl_hit_time = time.time()
            logger.info("Trade %s closed externally (SL/TP). クールダウン開始 (%d分).",
                        pos.trade_id, _COOLDOWN_AFTER_SL_SECONDS // 60)
            registry.remove(pos.trade_id)
            continue

        # Get current price (real-time tick if available, else M1 close)
        current_price = None
        if hasattr(broker, "get_current_price"):
            current_price = broker.get_current_price()
        if current_price is None:
            df_m1 = broker.fetch_candles(granularity=config.TF_M1, count=5)
            if df_m1 is None or df_m1.empty:
                continue
            current_price = float(df_m1["close"].iloc[-1])

        decision = evaluate_exit(pos, current_price, broker)

        logger.info(
            "ポジション管理 [%s] %s  現在P/L=+%.1fpips  %s",
            pos.trade_id, pos.signal.value,
            decision.current_profit_pips, decision.reason,
        )

        if decision.should_exit:
            if dry_run:
                logger.info("[DRY-RUN] 1分足反転シグナル → クローズ予定 (実際には出しません)")
            else:
                closed = broker.close_trade(pos.trade_id)
                if closed:
                    logger.info("1分足反転シグナルによりクローズ。+%.1fpips", decision.current_profit_pips)
                    registry.remove(pos.trade_id)


# ── Entry Cycle ────────────────────────────────────────────────────────────────

def run_entry_cycle(broker: OANDABroker, dry_run: bool) -> None:
    """Execute one entry evaluation cycle."""
    now = datetime.now(timezone.utc)
    logger.info("─── エントリーサイクル: %s ───", now.strftime("%Y-%m-%d %H:%M UTC"))

    # 1. Fetch primary (M15) candles
    df = broker.fetch_candles()
    if df is None or df.empty:
        logger.warning("M15データ取得失敗。スキップ。")
        return

    # 2. Build multi-timeframe context (H1, H4, M5)
    try:
        mtf = build_mtf_context(broker)
    except Exception as exc:
        logger.warning("MTFコンテキスト取得エラー (継続): %s", exc)
        mtf = None

    if mtf:
        logger.info(
            "上位足 H1=%s  H4=%s  レジスタンス=%s  サポート=%s",
            mtf.h1_trend, mtf.h4_trend,
            [f"{r:.3f}" for r in mtf.key_resistances()[:3]],
            [f"{s:.3f}" for s in mtf.key_supports()[:3]],
        )

    # 3. Evaluate strategy
    setup = evaluate(df, now=now, mtf_context=mtf)

    logger.info(
        "シグナル: %-5s  状態: %s  理由: %s",
        setup.signal.value,
        setup.market_state.value if setup.market_state else "-",
        setup.reason,
    )

    if setup.signal == Signal.NONE:
        return

    # 4. Log setup details
    logger.info(
        "  エントリー=%.3f  SL=%.3f(%.1fpips)  TP=%.3f(%.1fpips)  [%s]",
        setup.entry_price,
        setup.stop_loss, setup.risk_pips,
        setup.take_profit, setup.reward_pips,
        setup.entry_type,
    )

    # 5. Guard: no double entry
    if broker.has_open_trade() or len(registry) > 0:
        logger.info("既存ポジションあり。新規エントリースキップ。")
        return

    # 5b. Guard: cooldown after SL hit
    elapsed = time.time() - _last_sl_hit_time
    if elapsed < _COOLDOWN_AFTER_SL_SECONDS:
        remaining = int((_COOLDOWN_AFTER_SL_SECONDS - elapsed) / 60)
        logger.info("SL後クールダウン中。あと%d分待機。", remaining)
        return

    # 6. Reject if R:R is too low
    if setup.reward_pips > 0 and (setup.reward_pips / max(setup.risk_pips, 1)) < config.MIN_RISK_REWARD:
        logger.warning(
            "R:R不足 (%.1f:%.1f)。スキップ。",
            setup.risk_pips, setup.reward_pips,
        )
        return

    # 7. Place order
    if dry_run:
        logger.info("[DRY-RUN] %sオーダー予定。実際には出しません。", setup.signal.value)
        return

    trade_id = broker.place_order(setup)
    if trade_id:
        registry.add(OpenPosition(
            trade_id=trade_id,
            signal=setup.signal,
            entry_price=setup.entry_price,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            units=config.UNITS,
        ))
        logger.info("エントリー完了。trade_id=%s", trade_id)
    else:
        logger.error("注文失敗。")


# ── Main Loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="USD/JPY 自動売買ボット")
    parser.add_argument("--dry-run", action="store_true",
                        help="シグナル確認のみ。実際の注文は出しません。")
    parser.add_argument(
        "--broker",
        choices=["oanda", "mt5"],
        default=config.BROKER,
        help="ブローカー選択: oanda (デフォルト) または mt5",
    )
    args = parser.parse_args()

    _setup_logging()
    logger.info("=" * 60)
    logger.info("USD/JPY 自動売買ボット 起動")
    logger.info("モード    : %s", "DRY-RUN" if args.dry_run else "LIVE")
    logger.info("ブローカー: %s", args.broker.upper())
    if args.broker == "oanda":
        logger.info("環境      : %s", config.OANDA_ENVIRONMENT)
    else:
        logger.info("MT5サーバ : %s", config.MT5_SERVER)
    logger.info("通貨ペア  : %s  %s足", config.INSTRUMENT, config.GRANULARITY)
    logger.info("ポーリング: %d秒", config.POLL_INTERVAL_SECONDS)
    logger.info("静観時間帯: 01:00–08:00 JST (UTC 16:00–23:00)")
    logger.info("ストップ  : %dpips  ブレイクイーブン: %dpips後",
                config.STOP_LOSS_PIPS, config.BREAKEVEN_TRIGGER_PIPS)
    logger.info("=" * 60)

    # Validate credentials
    if args.broker == "oanda":
        if not config.OANDA_API_KEY or not config.OANDA_ACCOUNT_ID:
            logger.error("OANDA_API_KEY / OANDA_ACCOUNT_ID が未設定。.envを確認。")
            sys.exit(1)
    elif args.broker == "mt5":
        if not config.MT5_LOGIN or not config.MT5_PASSWORD or not config.MT5_SERVER:
            logger.error("MT5_LOGIN / MT5_PASSWORD / MT5_SERVER が未設定。.envを確認。")
            sys.exit(1)

    broker = _create_broker(args.broker)
    balance = broker.get_account_balance()
    if balance is not None:
        logger.info("口座残高: %.2f JPY", balance)

    tick = 0
    try:
        while True:
            tick += 1
            try:
                # ── Manage open positions every tick ──────────────────────────
                manage_open_positions(broker, dry_run=args.dry_run)

                # ── Look for new entries every tick ───────────────────────────
                run_entry_cycle(broker, dry_run=args.dry_run)

            except Exception as exc:
                logger.exception("サイクルエラー(継続): %s", exc)

            time.sleep(config.POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("ボット停止 (Ctrl+C)")


if __name__ == "__main__":
    main()
