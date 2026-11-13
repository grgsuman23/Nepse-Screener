"""Preset screens (evaluated daily). Add your own in config/screens.yaml."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import ROOT, load_yaml
from .dsl import DSLError, Evaluator
from .utils import log

PRESETS = [
    # ---------------- technical
    {"id": "golden_cross", "group": "Technical", "name": "Golden Cross (50/200) in last 5 days",
     "expr": "bars_since(cross_above(sma_50, sma_200)) <= 5 and liquid == 1"},
    {"id": "death_cross", "group": "Technical", "name": "Death Cross (50/200) in last 5 days",
     "expr": "bars_since(cross_below(sma_50, sma_200)) <= 5"},
    {"id": "breakout_52w", "group": "Technical", "name": "52-week high breakout on volume",
     "expr": "close > hh_250 and vol_ratio > 1.5 and liquid == 1"},
    {"id": "breakout_55", "group": "Technical", "name": "55-day breakout (Turtle) with 2× volume",
     "expr": "close > hh_55 and vol_ratio > 2 and chg_pct > 0.02 and liquid == 1"},
    {"id": "pullback_uptrend", "group": "Technical", "name": "Pullback in uptrend (RSI-14 < 40 above 200-DMA)",
     "expr": "close > sma_200 and ema_50 > sma_200 and rsi_14 < 40"},
    {"id": "rsi2_oversold", "group": "Technical", "name": "RSI-2 oversold in uptrend",
     "expr": "rsi_2 < 10 and close > sma_200 and liquid == 1"},
    {"id": "macd_bull", "group": "Technical", "name": "MACD bullish crossover below zero",
     "expr": "cross_above(macd, macd_signal) and macd < 0"},
    {"id": "supertrend_flip", "group": "Technical", "name": "Supertrend flipped bullish",
     "expr": "st_dir > 0 and shift(st_dir, 1) < 0"},
    {"id": "volume_spike", "group": "Technical", "name": "Accumulation: volume spike on up day",
     "expr": "vol_ratio > 2.5 and chg_pct > 0.02 and close > open"},
    {"id": "bb_squeeze", "group": "Technical", "name": "Bollinger squeeze (volatility at 6-month low)",
     "expr": "bb_width < lowest(bb_width, 120) * 1.1 and liquid == 1"},
    {"id": "strong_trend", "group": "Technical", "name": "Strong uptrend (ADX > 25, +DI > -DI)",
     "expr": "adx_14 > 25 and plus_di > minus_di and close > ema_50"},
    {"id": "rs_leaders", "group": "Technical", "name": "Relative-strength leaders (RS > 85)",
     "expr": "rs_rating > 85 and close > ema_50 and liquid == 1"},
    {"id": "overbought", "group": "Technical", "name": "Overbought / overextended (profit-booking watch)",
     "expr": "rsi_14 > 78 and dist_ema50 > 0.2"},
    # ---------------- fundamental
    {"id": "value_vs_sector", "group": "Fundamental", "name": "Cheaper than sector (P/E & P/B below median)",
     "expr": "pe > 0 and pe_vs_sector < 0.85 and pbv_vs_sector < 0.9 and roe > 0.08"},
    {"id": "high_dividend", "group": "Fundamental", "name": "High dividend yield (> 5%)",
     "expr": "div_yield > 0.05 and eps > 0"},
    {"id": "graham", "group": "Fundamental", "name": "Graham number above price (> 20% margin)",
     "expr": "graham_discount > 0.2"},
    {"id": "high_roe", "group": "Fundamental", "name": "High ROE (> 15%) and P/E < 25",
     "expr": "roe > 0.15 and pe > 0 and pe < 25"},
    {"id": "loss_makers", "group": "Fundamental", "name": "Loss-making companies (avoid / review)",
     "expr": "eps <= 0"},
    # ---------------- combined
    {"id": "quality_momentum", "group": "Combined", "name": "Quality momentum (top ROE in sector + RS > 70 + uptrend)",
     "expr": "roe_pct_sector > 0.6 and rs_rating > 70 and close > sma_200 and liquid == 1"},
    {"id": "value_turnaround", "group": "Combined", "name": "Value turnaround (cheap + Supertrend turned up)",
     "expr": "pe_vs_sector < 0.9 and eps > 0 and st_dir > 0 and bars_since(cross_above(close, st_line)) <= 10"},
    {"id": "model_strong_buy", "group": "Model", "name": "Model: STRONG BUY",
     "expr": "stance_code == 2"},
    {"id": "model_buy", "group": "Model", "name": "Model: BUY or better", "expr": "stance_code >= 1"},
    {"id": "model_exit", "group": "Model", "name": "Model: EXIT", "expr": "stance_code == -2"},
]


def all_presets(config_dir: Path | None = None) -> list[dict]:
    extra = []
    f = (config_dir or ROOT / "config") / "screens.yaml"
    if f.exists():
        extra = load_yaml(f).get("screens", []) or []
        for s in extra:
            s.setdefault("group", "My screens")
    return PRESETS + extra


def run_presets(w: dict[str, pd.DataFrame], presets: list[dict], at: pd.Timestamp | None = None) -> dict[str, list[str]]:
    at = at if at is not None else w["close"].index[-1]
    # evaluate on a recent window only (fast)
    window = {k: v.loc[:at].iloc[-260:] for k, v in w.items()}
    ev = Evaluator(window)
    hits = {}
    for p in presets:
        try:
            missing = ev.validate(p["expr"])
            if missing:
                hits[p["id"]] = []
                continue
            m = ev.mask(p["expr"])
            row = m.loc[at] if at in m.index else m.iloc[-1]
            hits[p["id"]] = sorted(row[row].index.tolist())
        except DSLError as e:
            log.warning("Screen %s failed: %s", p["id"], e)
            hits[p["id"]] = []
    return hits
