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
    reg = pd.Series(0, index=m.index)
    reg[bull] = 1
    reg[bear] = -1
    b50 = m["breadth_50"]
    reg[(reg == 1) & (b50 < rc.get("breadth_bear", 0.35))] = 0
    reg[(reg == -1) & (b50 > rc.get("breadth_bull", 0.55))] = 0
    # before the slow average exists, fall back to the fast one
    early = m["sma_slow"].isna() & m["ema_fast"].notna()
    reg[early] = np.where(m.loc[early, "index"] > m.loc[early, "ema_fast"], 1, -1)
    m["regime"] = reg.astype(int)
    m["mkt_ret_1m"] = m["index"] / m["index"].shift(21) - 1
    m["mkt_ret_3m"] = m["index"] / m["index"].shift(63) - 1
    m["mkt_ret_6m"] = m["index"] / m["index"].shift(126) - 1
    return m


REGIME_LABEL = {1: "Bull", 0: "Neutral / Sideways", -1: "Bear"}


def sector_analysis(close: pd.DataFrame, sectors: dict[str, str], market_index: pd.Series,
                    liquid: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Returns (sector_rs_rank wide frame per stock, {sector: frame})."""
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
        sec_frames[sec] = f
        blend[sec] = 0.5 * f["ret_1m"] + 0.5 * f["ret_3m"]
    blend_df = pd.DataFrame(blend)
    rank = blend_df.rank(axis=1, pct=True) * 100
    for sec, f in sec_frames.items():
        f["rs_rank"] = rank[sec]
    stock_rank = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for sec, syms in by.items():
        stock_rank[syms] = np.repeat(rank[sec].to_numpy()[:, None], len(syms), axis=1)
    return stock_rank, sec_frames


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
