"""
簡易バックテスト
────────────────────────────────────────────────────────────────────────────────
OANDAから過去データを取得し、戦略シグナルをシミュレートします。
実際の注文は出しません。

Usage:
    python backtest.py [--count 500]
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timezone

import pandas as pd

from trading_bot import config
from trading_bot.broker import OANDABroker
from trading_bot.strategy import Signal, evaluate

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("backtest")


def run_backtest(df: pd.DataFrame) -> None:
    """
    Walk-forward simulation over the provided OHLC DataFrame.
    Uses a rolling window of 50 bars to evaluate each candle.
    """
    window = 50
    results = []

    print(f"\n{'='*60}")
    print(f"バックテスト開始  足数={len(df)}  ウィンドウ={window}")
    print(f"{'='*60}\n")

    for i in range(window, len(df)):
        slice_df = df.iloc[i - window: i].copy()
        candle_time = df.index[i - 1]

        setup = evaluate(slice_df, now=candle_time.to_pydatetime().replace(tzinfo=timezone.utc))
        if setup.signal == Signal.NONE:
            continue

        results.append({
            "time": candle_time,
            "signal": setup.signal.value,
            "entry": setup.entry_price,
            "sl": setup.stop_loss,
            "tp": setup.take_profit,
            "risk_pips": setup.risk_pips,
            "reward_pips": setup.reward_pips,
            "reason": setup.reason,
        })

    if not results:
        print("シグナルなし")
        return

    res_df = pd.DataFrame(results)
    print(res_df[["time", "signal", "entry", "sl", "tp", "risk_pips", "reward_pips"]].to_string(index=False))

    print(f"\n{'='*60}")
    print(f"合計シグナル数 : {len(results)}")
    print(f"  ロング       : {(res_df['signal'] == 'LONG').sum()}")
    print(f"  ショート     : {(res_df['signal'] == 'SHORT').sum()}")
    print(f"平均リスク(pips): {res_df['risk_pips'].mean():.1f}")
    print(f"平均リワード(pips): {res_df['reward_pips'].mean():.1f}")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="USD/JPY 戦略バックテスト")
    parser.add_argument("--count", type=int, default=500, help="取得する足数 (最大5000)")
    args = parser.parse_args()

    if not config.OANDA_API_KEY or not config.OANDA_ACCOUNT_ID:
        print("エラー: OANDA_API_KEY / OANDA_ACCOUNT_ID が設定されていません。")
        sys.exit(1)

    broker = OANDABroker()
    print(f"OANDAからデータ取得中... ({config.INSTRUMENT} {config.GRANULARITY} ×{args.count})")

    df = broker.fetch_candles(count=min(args.count, 5000))
    if df is None or df.empty:
        print("データ取得失敗")
        sys.exit(1)

    print(f"取得完了: {len(df)} 本  ({df.index[0]} ～ {df.index[-1]})")
    run_backtest(df)


if __name__ == "__main__":
    main()
