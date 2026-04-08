"""
USD/JPY マルチタイムフレーム戦略
────────────────────────────────────────────────────────────────────────────────
トレードルーティン実装:

【環境認識】
  水平線（レジサポ / 押し / 戻し）をHTFで特定 → timeframes.py

【トレンド/レンジ判定】
  トレンド: 各MAが順番通り / 高値・安値の更新継続 / 強い実体のある足
  レンジ  : 高値・安値を更新しない / MA収束 / シグナル乱発

【エントリー条件】
  トレンド: 押し・戻しのPOで順張り + HTF一致 + RSI極値フィルター
  レンジ  : (a)ブレイクアウト狙い (b)上限・下限での逆張り

【TP/SL】
  TP: 直近レジサポ / 前回高値・安値
  SL: エントリー根拠が崩れた箇所

【禁則事項】
  - 夜中（01:00–08:00 JST）は静観
  - 大きな指標前（30分）は静観
  - 飛び乗り禁止（価格がMAから離れすぎ）
  - 方向感ゼロのときは静観
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from trading_bot import config
from trading_bot.indicators import (
    calculate_ma,
    calculate_rsi,
    compute_all_indicators,
    detect_range,
    dow_breakout,
    ma_alignment,
    ma_direction,
    rsi_extreme_filter,
    sma_crossover,
)

logger = logging.getLogger(__name__)


# ── Enums & Data Classes ───────────────────────────────────────────────────────

class Signal(Enum):
    LONG  = "LONG"
    SHORT = "SHORT"
    NONE  = "NONE"


class MarketState(Enum):
    TREND_UP    = "TREND_UP"
    TREND_DOWN  = "TREND_DOWN"
    RANGE       = "RANGE"
    UNCERTAIN   = "UNCERTAIN"


@dataclass
class TradeSetup:
    signal: Signal
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    market_state: MarketState = MarketState.UNCERTAIN
    entry_type: str = ""          # 'trend_po' | 'range_breakout' | 'range_reversal'
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
    深夜の時間帯（夜中の1時以降〜朝の8時まで）は静観。
    01:00–08:00 JST = 16:00–23:00 UTC
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    # No-trade: 16:00 <= hour < 23:00 UTC  (= 01:00–08:00 JST)
    if 16 <= hour < 23:
        logger.debug("深夜時間帯 UTC %02d:xx。静観します。", hour)
        return False
    return True


# ── Market State Detection ─────────────────────────────────────────────────────

def _candle_quality(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    強い実体のある足が多いかどうかを評価。
    トレンドの質確認: ヒゲの少ない実体のあるローソク足が多い

    Returns:
      body_ratio   : average body/range ratio (1.0 = no wicks)
      strong_candles: fraction of candles with body > 60% of range
    """
    recent = df.tail(lookback)
    bodies = (recent["close"] - recent["open"]).abs()
    ranges = (recent["high"] - recent["low"]).replace(0, np.nan)
    ratios = (bodies / ranges).dropna()
    body_ratio = float(ratios.mean()) if not ratios.empty else 0.5
    strong_frac = float((ratios > 0.6).mean()) if not ratios.empty else 0.0
    return {"body_ratio": body_ratio, "strong_candle_fraction": strong_frac}


def classify_market(df: pd.DataFrame) -> tuple[MarketState, dict]:
    """
    Classify market as TREND_UP / TREND_DOWN / RANGE / UNCERTAIN.

    トレンド: 各MAが順番通り並んでいる + 高値・安値更新継続 + 強い実体
    レンジ  : MA収束 + 高値・安値更新なし
    不明    : どちらとも判定できない → 静観
    """
    close = df["close"]

    ma3   = calculate_ma(close, config.MA_PERIOD)
    sma20 = calculate_ma(close, config.SMA_MID)
    sma50 = calculate_ma(close, config.SMA_LONG)
    rsi   = calculate_rsi(close, config.RSI_PERIOD)

    alignment = ma_alignment(ma3, sma20, sma50)
    wave_ind  = compute_all_indicators(df, config.MA_PERIOD, config.RSI_PERIOD, config.WAVE_LOOKBACK)
    range_info = detect_range(df, ma3, sma20, sma50, config.WAVE_LOOKBACK)
    candle_q   = _candle_quality(df)

    latest_rsi = float(rsi.iloc[-1]) if not rsi.empty else 50.0

    meta = {
        "alignment": alignment,
        "wave_trend": wave_ind["wave_trend"],
        "is_range":   range_info["is_range"],
        "range_high": range_info["range_high"],
        "range_low":  range_info["range_low"],
        "ma_cluster": range_info["ma_cluster"],
        "range_bounces": range_info["range_bounces"],
        "candle_quality": candle_q,
        "rsi": latest_rsi,
        "ma3":   float(ma3.iloc[-1])   if not ma3.empty else 0.0,
        "sma20": float(sma20.iloc[-1]) if not sma20.empty else 0.0,
        "sma50": float(sma50.iloc[-1]) if not sma50.empty else 0.0,
        "close": float(close.iloc[-1]),
    }

    # Clearly ranging
    if range_info["is_range"]:
        return MarketState.RANGE, meta

    # Uptrend: MA bullish aligned + higher highs & lows + decent candle bodies
    if alignment == "bullish_aligned" and wave_ind["wave_trend"] == "uptrend":
        return MarketState.TREND_UP, meta

    # Downtrend: MA bearish aligned + lower highs & lows
    if alignment == "bearish_aligned" and wave_ind["wave_trend"] == "downtrend":
        return MarketState.TREND_DOWN, meta

    # Otherwise uncertain
    return MarketState.UNCERTAIN, meta


# ── Anti-Chasing Filter ────────────────────────────────────────────────────────

def _check_no_chasing(entry: float, ma3: float, signal: Signal) -> tuple[bool, str]:
    """
    飛び乗りは絶対ダメ！
    Reject entry if price has moved too far from the 3MA.

    For a long: price should be close to (or just above) 3MA, not far above.
    For a short: price should be close to (or just below) 3MA, not far below.
    """
    pip = config.PIP_VALUE_JPY
    distance_pips = abs(entry - ma3) / pip
    max_dist = config.MAX_ENTRY_DISTANCE_FROM_MA_PIPS

    if distance_pips > max_dist:
        return False, (f"飛び乗り禁止: 価格({entry:.3f})が3MA({ma3:.3f})から"
                       f"{distance_pips:.1f}pips離れています (最大{max_dist}pips)")
    return True, f"飛び乗りチェックOK: MAとの距離={distance_pips:.1f}pips"


# ── RSI Filter ─────────────────────────────────────────────────────────────────

def _rsi_allows_entry(rsi: float, timeframe: str = "M15") -> tuple[bool, str]:
    """
    RSIが70or30を超えていないか？
    M5 → 注意（entry caution but allowed）
    M15以上 → エントリーは控える（block entry）
    """
    check = rsi_extreme_filter(rsi, timeframe)
    if check["advisory"] == "avoid":
        return False, check["detail"]
    return True, check["detail"]


# ── PO (Point of Order) Signal ─────────────────────────────────────────────────

def _po_signal(df: pd.DataFrame) -> dict:
    """
    PO = SMAのゴールデン/デッドクロス (エントリーのシグナル点灯).
    短期SMA(5)が中期SMA(20)を抜けた場合にシグナル。

    Returns dict with:
      signal     : 'bullish_cross' | 'bearish_cross' | 'none'
      sma5_slope : direction of SMA5
      sma20_slope: direction of SMA20
    """
    close = df["close"]
    sma5  = calculate_ma(close, config.SMA_SHORT)
    sma20 = calculate_ma(close, config.SMA_MID)
    cross = sma_crossover(sma5, sma20)
    return {
        "signal":      cross,
        "sma5_slope":  ma_direction(sma5),
        "sma20_slope": ma_direction(sma20),
        "sma5_val":    float(sma5.iloc[-1]) if not sma5.empty else 0.0,
        "sma20_val":   float(sma20.iloc[-1]) if not sma20.empty else 0.0,
    }


# ── Stop Loss / Take Profit ────────────────────────────────────────────────────

def _calc_sl_tp(signal: Signal, entry: float, ind: dict,
                range_high: float = 0.0, range_low: float = 0.0) -> tuple[float, float]:
    """
    TP: 直近レジサポ / 前回高値・安値
    SL: エントリー根拠が崩れた箇所 (swing high/low + buffer)
    """
    pip = config.PIP_VALUE_JPY
    fixed_sl = config.STOP_LOSS_PIPS * pip

    if signal == Signal.LONG:
        swing_sl = ind.get("last_swing_low")
        if swing_sl and (entry - swing_sl) > fixed_sl:
            sl = swing_sl - pip * 2
        else:
            sl = entry - fixed_sl

        # TP: nearest resistance (swing high) or fixed 2:1
        swing_tp = ind.get("last_swing_high")
        risk = entry - sl
        if swing_tp and swing_tp > entry and (swing_tp - entry) >= risk:
            tp = swing_tp - pip  # 1 pip before resistance
        elif range_high and range_high > entry:
            tp = range_high - pip
        else:
            tp = entry + risk * 2

    else:  # SHORT
        swing_sl = ind.get("last_swing_high")
        if swing_sl and (swing_sl - entry) > fixed_sl:
            sl = swing_sl + pip * 2
        else:
            sl = entry + fixed_sl

        swing_tp = ind.get("last_swing_low")
        risk = sl - entry
        if swing_tp and swing_tp < entry and (entry - swing_tp) >= risk:
            tp = swing_tp + pip
        elif range_low and range_low < entry:
            tp = range_low + pip
        else:
            tp = entry - risk * 2

    return round(sl, 3), round(tp, 3)


# ── Trend Trade Evaluation ─────────────────────────────────────────────────────

def _evaluate_trend_entry(df: pd.DataFrame, state: MarketState,
                          meta: dict, mtf_context=None) -> TradeSetup:
    """
    トレンド相場でのエントリー評価。
    押し・戻しのPOで順張り。

    Rules:
      ① MAの並びが明確 (alignment check in meta)
      ② 高値/安値の更新が継続
      ③ PO signal (SMA5×SMA20 crossover)
      ④ HTF一致 (H1 / H4)
      ⑤ RSI極値フィルター
      ⑥ 飛び乗り禁止
    """
    no_trade = TradeSetup(signal=Signal.NONE, entry_price=0.0,
                          stop_loss=0.0, take_profit=0.0, reason="",
                          market_state=state)

    signal = Signal.LONG if state == MarketState.TREND_UP else Signal.SHORT

    ind = compute_all_indicators(df, config.MA_PERIOD, config.RSI_PERIOD, config.WAVE_LOOKBACK)
    po  = _po_signal(df)
    entry = ind["close"]

    reasons_ok = []
    reasons_fail = []

    # ① MA alignment
    if meta["alignment"] in ("bullish_aligned", "bearish_aligned"):
        reasons_ok.append(f"MA整列({meta['alignment']})")
    else:
        reasons_fail.append(f"MA整列NG({meta['alignment']})")

    # ② Wave trend
    expected_wave = "uptrend" if signal == Signal.LONG else "downtrend"
    if ind["wave_trend"] == expected_wave:
        reasons_ok.append(f"波形={ind['wave_trend']}")
    else:
        reasons_fail.append(f"波形NG({ind['wave_trend']})")

    # ③ PO signal
    expected_cross = "bullish_cross" if signal == Signal.LONG else "bearish_cross"
    if po["signal"] == expected_cross:
        reasons_ok.append(f"POシグナル点灯({po['signal']})")
    else:
        reasons_fail.append(f"POシグナルなし({po['signal']})")

    # ④ HTF filter (H1 must agree)
    if mtf_context is not None:
        if signal == Signal.LONG and not mtf_context.htf_allows_long():
            reasons_fail.append(f"上位足NG(H1={mtf_context.h1_trend})")
        elif signal == Signal.SHORT and not mtf_context.htf_allows_short():
            reasons_fail.append(f"上位足NG(H1={mtf_context.h1_trend})")
        else:
            reasons_ok.append(f"上位足OK(H1={mtf_context.h1_trend})")

    # ⑤ RSI extreme filter
    rsi_ok, rsi_msg = _rsi_allows_entry(ind["rsi"], config.GRANULARITY)
    if rsi_ok:
        reasons_ok.append(rsi_msg)
    else:
        reasons_fail.append(rsi_msg)

    # ⑥ Anti-chasing
    chase_ok, chase_msg = _check_no_chasing(entry, ind["ma3"], signal)
    if chase_ok:
        reasons_ok.append(chase_msg)
    else:
        reasons_fail.append(chase_msg)

    if reasons_fail:
        no_trade.reason = "条件未達: " + " | ".join(reasons_fail)
        no_trade.indicators = ind
        return no_trade

    # All conditions met → build setup
    sl, tp = _calc_sl_tp(signal, entry, ind)
    direction = "買い" if signal == Signal.LONG else "売り"
    return TradeSetup(
        signal=signal,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        reason=f"【{direction}エントリー(トレンド順張り)】 " + " / ".join(reasons_ok),
        market_state=state,
        entry_type="trend_po",
        indicators=ind,
    )


# ── Range Trade Evaluation ─────────────────────────────────────────────────────

def _evaluate_range_entry(df: pd.DataFrame, meta: dict) -> TradeSetup:
    """
    レンジ相場でのエントリー評価。

    (a) レンジ抜けブレイクアウト: 20/50MAが傾き始め + 足がレンジ外で確定
    (b) レンジ内逆張り: 上限/下限でRSI極値 + 1分Dow崩れ相当（終値での反発確認）
    (c) ノーエントリー: MA中央付近でシグナル乱発

    Returns TradeSetup (NONE if no valid range entry).
    """
    no_trade = TradeSetup(signal=Signal.NONE, entry_price=0.0,
                          stop_loss=0.0, take_profit=0.0, reason="",
                          market_state=MarketState.RANGE)

    ind      = compute_all_indicators(df, config.MA_PERIOD, config.RSI_PERIOD, config.WAVE_LOOKBACK)
    po       = _po_signal(df)
    close    = df["close"].iloc[-1]
    pip      = config.PIP_VALUE_JPY
    rh       = meta["range_high"]
    rl       = meta["range_low"]
    range_w  = (rh - rl) / pip

    # (c) No entry if MA cluster at midpoint with random signals
    if meta["ma_cluster"] and po["signal"] != "none":
        no_trade.reason = "レンジ中央でシグナル乱発 → ノーエントリー"
        return no_trade

    # Minimum range width to be worth trading
    if range_w < 15:
        no_trade.reason = f"レンジ幅が狭い({range_w:.1f}pips)"
        return no_trade

    # ── (a) Range breakout ────────────────────────────────────────────────────
    dow = dow_breakout(df, config.WAVE_LOOKBACK)
    sma20 = calculate_ma(df["close"], config.SMA_MID)
    sma50 = calculate_ma(df["close"], config.SMA_LONG)
    sma20_dir = ma_direction(sma20)
    sma50_dir = ma_direction(sma50)

    breakout_long  = (close > rh and dow == "bullish"
                      and sma20_dir == "up" and sma50_dir in ("up", "flat"))
    breakout_short = (close < rl and dow == "bearish"
                      and sma20_dir == "down" and sma50_dir in ("down", "flat"))

    if breakout_long:
        sl_candidate = rh - pip * 3   # SL just inside the broken range
        min_sl = close - config.STOP_LOSS_PIPS * pip
        sl = min(sl_candidate, min_sl)  # Ensure minimum SL distance
        tp = rh + (rh - rl) * 0.8
        return TradeSetup(
            signal=Signal.LONG,
            entry_price=close,
            stop_loss=round(sl, 3),
            take_profit=round(tp, 3),
            reason=f"【買いエントリー(レンジ上方ブレイク)】 上限{rh:.3f}を突破 / 20MA:{sma20_dir} / 50MA:{sma50_dir}",
            market_state=MarketState.RANGE,
            entry_type="range_breakout",
            indicators=ind,
        )

    if breakout_short:
        sl_candidate = rl + pip * 3
        min_sl = close + config.STOP_LOSS_PIPS * pip
        sl = max(sl_candidate, min_sl)  # Ensure minimum SL distance
        tp = rl - (rh - rl) * 0.8
        return TradeSetup(
            signal=Signal.SHORT,
            entry_price=close,
            stop_loss=round(sl, 3),
            take_profit=round(tp, 3),
            reason=f"【売りエントリー(レンジ下方ブレイク)】 下限{rl:.3f}を突破 / 20MA:{sma20_dir} / 50MA:{sma50_dir}",
            market_state=MarketState.RANGE,
            entry_type="range_breakout",
            indicators=ind,
        )

    # ── (b) Range reversal at limits ─────────────────────────────────────────
    dist_from_high = (rh - close) / pip
    dist_from_low  = (close - rl) / pip
    rsi_val = ind["rsi"]

    # Near upper limit → potential short reversal
    if dist_from_high <= 8 and rsi_val >= config.RSI_UPPER - 5:
        _, rsi_msg = _rsi_allows_entry(rsi_val, "M5")  # caution ok for M5
        sl = rh + pip * 5
        tp = rl + (rh - rl) * 0.3
        return TradeSetup(
            signal=Signal.SHORT,
            entry_price=close,
            stop_loss=round(sl, 3),
            take_profit=round(tp, 3),
            reason=f"【売りエントリー(レンジ上限逆張り)】 上限{rh:.3f}付近({dist_from_high:.1f}pips) / RSI={rsi_val:.1f}",
            market_state=MarketState.RANGE,
            entry_type="range_reversal",
            indicators=ind,
        )

    # Near lower limit → potential long reversal
    if dist_from_low <= 8 and rsi_val <= config.RSI_LOWER + 5:
        sl = rl - pip * 5
        tp = rh - (rh - rl) * 0.3
        return TradeSetup(
            signal=Signal.LONG,
            entry_price=close,
            stop_loss=round(sl, 3),
            take_profit=round(tp, 3),
            reason=f"【買いエントリー(レンジ下限逆張り)】 下限{rl:.3f}付近({dist_from_low:.1f}pips) / RSI={rsi_val:.1f}",
            market_state=MarketState.RANGE,
            entry_type="range_reversal",
            indicators=ind,
        )

    no_trade.reason = f"レンジ内・エントリー条件なし(価格={close:.3f} 上={rh:.3f} 下={rl:.3f})"
    no_trade.indicators = ind
    return no_trade


# ── Main Evaluation Entry Point ────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, now: Optional[datetime] = None,
             mtf_context=None) -> TradeSetup:
    """
    Evaluate market and return a TradeSetup.

    Parameters
    ----------
    df          : M15 OHLC DataFrame
    now         : current UTC datetime
    mtf_context : MultiTFContext from timeframes.py (optional)
    """
    no_trade = TradeSetup(signal=Signal.NONE, entry_price=0.0,
                          stop_loss=0.0, take_profit=0.0, reason="")

    # ── Guard: data ────────────────────────────────────────────────────────────
    if df is None or len(df) < max(config.SMA_LONG, config.RSI_PERIOD) + 10:
        no_trade.reason = "データ不足"
        return no_trade

    # ── Rule: time filter ─────────────────────────────────────────────────────
    if not is_trading_hours(now):
        no_trade.reason = "深夜時間帯のため静観"
        return no_trade

    # ── Market state classification ────────────────────────────────────────────
    state, meta = classify_market(df)

    logger.info(
        "市場状態: %s  MA整列=%s  Wave=%s  RSI=%.1f",
        state.value, meta["alignment"], meta["wave_trend"], meta["rsi"],
    )

    # ── Uncertain → no trade ──────────────────────────────────────────────────
    if state == MarketState.UNCERTAIN:
        no_trade.reason = (f"方向感不明のため静観"
                           f"(MA={meta['alignment']}, Wave={meta['wave_trend']})")
        no_trade.indicators = meta
        return no_trade

    # ── Trend entries ─────────────────────────────────────────────────────────
    if state in (MarketState.TREND_UP, MarketState.TREND_DOWN):
        return _evaluate_trend_entry(df, state, meta, mtf_context)

    # ── Range entries ─────────────────────────────────────────────────────────
    if state == MarketState.RANGE:
        return _evaluate_range_entry(df, meta)

    no_trade.reason = "評価不能"
    return no_trade
