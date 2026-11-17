"""Market index, breadth, regime and sector analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import ema, sma


def computed_index(close: pd.DataFrame, liquid: pd.DataFrame | None = None) -> pd.Series:
    """Equal-weight index of (liquid) stocks — used when the official NEPSE
    index history is unavailable."""
    rets = close.pct_change(fill_method=None)
    if liquid is not None:
        rets = rets.where(liquid.shift().fillna(False).astype(bool))
    r = rets.clip(-0.11, 0.11).mean(axis=1).fillna(0)
    return 1000 * (1 + r).cumprod()


def breadth(close: pd.DataFrame, w: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = pd.DataFrame(index=close.index)
    for n in (50, 200):
        ma = w[f"sma_{n}"]
        valid = ma.notna()
        out[f"breadth_{n}"] = (close > ma).where(valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)
    chg = close.pct_change(fill_method=None)
    out["advances"] = (chg > 0).sum(axis=1)
    out["declines"] = (chg < 0).sum(axis=1)
    out["new_highs"] = (w["high"] >= w["high_52w_t"]).sum(axis=1)
    out["new_lows"] = (w["low"] <= w["low_52w_t"]).sum(axis=1)
    return out


def regime_frame(index_close: pd.Series, br: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    rc = cfg.get("regime", {})
    fast, slow = rc.get("fast_ma", 50), rc.get("slow_ma", 200)
    m = pd.DataFrame({"index": index_close})
    m["ema_fast"] = ema(m["index"], fast)
    m["sma_slow"] = sma(m["index"], slow)
    m = m.join(br, how="left")
    bull = (m["index"] > m["sma_slow"]) & (m["ema_fast"] > m["sma_slow"])
    bear = (m["index"] < m["sma_slow"]) & (m["ema_fast"] < m["sma_slow"])
    reg = pd.Series(0.0, index=m.index)
    reg[bull] = 1.0
    reg[bear] = -1.0
    b50 = m["breadth_50"]
    reg[(reg == 1) & (b50 < rc.get("breadth_bear", 0.35))] = 0.0
    reg[(reg == -1) & (b50 > rc.get("breadth_bull", 0.55))] = 0.0
    # before the slow average exists, fall back to the fast one
    early = m["sma_slow"].isna() & m["ema_fast"].notna()
    reg[early] = np.where(m.loc[early, "index"] > m.loc[early, "ema_fast"], 1.0, -1.0)
    m["thrust"] = False
    if rc.get("recovery_enabled", True):
        rec, thrust = recovery_mask(m, rc)
        m["thrust"] = thrust
        reg[rec & (reg < 1)] = 0.5
    m["regime"] = reg
    m["mkt_ret_1m"] = m["index"] / m["index"].shift(21) - 1
    m["mkt_ret_3m"] = m["index"] / m["index"].shift(63) - 1
    m["mkt_ret_6m"] = m["index"] / m["index"].shift(126) - 1
    return m


def recovery_mask(m: pd.DataFrame, rc: dict) -> tuple[pd.Series, pd.Series]:
    """Early-bottom detection.

    Recovery = index back above a RISING 50-EMA, plus either
      * a breadth thrust: share of stocks above their 50-DMA jumps from below
        `thrust_low` to above `thrust_high` within `thrust_window` sessions
        (remembered for `thrust_memory` sessions), or
      * more new 52-week highs than lows over 10 sessions with breadth > 50%."""
    b50 = m["breadth_50"]
    low, high = rc.get("thrust_low", 0.25), rc.get("thrust_high", 0.50)
    win, mem = int(rc.get("thrust_window", 15)), int(rc.get("thrust_memory", 60))
    thrust = (b50 > high) & (b50.rolling(win, min_periods=1).min().shift(1) < low)
    pos = pd.Series(np.where(thrust.to_numpy(), np.arange(len(m)), np.nan), index=m.index).ffill()
    alive = (np.arange(len(m)) - pos) <= mem
    nh = m["new_highs"].rolling(10, min_periods=1).sum()
    nl = m["new_lows"].rolling(10, min_periods=1).sum()
    turning = (m["index"] > m["ema_fast"]) & (m["ema_fast"] > m["ema_fast"].shift(5))
    rec = turning & (alive.fillna(False) | ((nh > nl) & (b50 > 0.5)))
    return rec.fillna(False), thrust.fillna(False)


def regime_name(r) -> str:
    """Numeric regime -> key used in settings (bull / recovery / neutral / bear)."""
    if r is None or r != r:
        return "neutral"
    return "bull" if r >= 1 else "recovery" if r > 0 else "bear" if r < 0 else "neutral"


REGIME_LABEL = {1: "Bull", 0.5: "Recovery", 0: "Neutral / Sideways", -1: "Bear"}


def regime_label(r) -> str:
    return {"bull": "Bull", "recovery": "Recovery", "neutral": "Neutral / Sideways", "bear": "Bear"}[regime_name(r)]


def sector_analysis(close: pd.DataFrame, sectors: dict[str, str], market_index: pd.Series,
                    liquid: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    """Returns (sector_rs_rank per stock, {sector: frame}, sector_regime per stock)."""
    rets = close.pct_change(fill_method=None).clip(-0.11, 0.11)
    by: dict[str, list[str]] = {}
    for s in close.columns:
        by.setdefault(sectors.get(s) or "Others", []).append(s)
    sec_frames, blend = {}, {}
    for sec, syms in by.items():
        r = rets[syms].mean(axis=1).fillna(0)
        idx = 1000 * (1 + r).cumprod()
        f = pd.DataFrame({"index": idx})
        for name, n in (("ret_1w", 5), ("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_12m", 250)):
            f[name] = idx / idx.shift(n) - 1
        ratio = idx / market_index.reindex(idx.index).ffill()
        f["rs_ratio"] = 100 * ratio / ratio.rolling(50, min_periods=20).mean()
        f["rs_mom"] = 100 * f["rs_ratio"] / f["rs_ratio"].shift(10)
        f["above_200"] = (close[syms] > close[syms].rolling(200, min_periods=150).mean()).mean(axis=1)
        f["n_stocks"] = len(syms)
        f["n_liquid"] = liquid[syms].sum(axis=1)
        # the sector's own trend regime (same rules as the market, no breadth)
        e50, s200 = ema(idx, 50), sma(idx, 200)
        sreg = pd.Series(0.0, index=idx.index)
        sreg[(idx > s200) & (e50 > s200)] = 1.0
        sreg[(idx < s200) & (e50 < s200)] = -1.0
        early = s200.isna() & e50.notna()
        sreg[early] = np.where(idx[early] > e50[early], 1.0, -1.0)
        f["regime"] = sreg
        sec_frames[sec] = f
        blend[sec] = 0.5 * f["ret_1m"] + 0.5 * f["ret_3m"]
    blend_df = pd.DataFrame(blend)
    rank = blend_df.rank(axis=1, pct=True) * 100
    for sec, f in sec_frames.items():
        f["rs_rank"] = rank[sec]
    stock_rank = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    stock_reg = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for sec, syms in by.items():
        stock_rank[syms] = np.repeat(rank[sec].to_numpy()[:, None], len(syms), axis=1)
        stock_reg[syms] = np.repeat(sec_frames[sec]["regime"].to_numpy()[:, None], len(syms), axis=1)
    return stock_rank, sec_frames, stock_reg


def quadrant(rs_ratio: float, rs_mom: float) -> str:
    if rs_ratio != rs_ratio or rs_mom != rs_mom:
        return "n/a"
    if rs_ratio >= 100 and rs_mom >= 100:
        return "Leading"
    if rs_ratio >= 100:
        return "Weakening"
    if rs_mom >= 100:
        return "Improving"
    return "Lagging"
