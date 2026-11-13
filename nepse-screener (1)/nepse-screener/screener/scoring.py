"""Multi-factor composite score (0-100) and stance (Strong Buy … Exit).

Factor design reflects how NEPSE actually behaves:
* strongly regime-driven (the index explains most of a stock's move), so the
  market regime gates every BUY;
* momentum persists for months (retail herding), so trend + relative
  strength carry the most weight;
* operator-driven spikes in illiquid names are common, so liquidity and
  volume confirmation matter;
* fundamentals are judged against SECTOR peers (a bank P/E is not a hydro P/E).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STANCES = {2: "STRONG BUY", 1: "BUY", 0: "HOLD", -1: "REDUCE", -2: "EXIT"}


def _pct(df: pd.DataFrame, mask: pd.DataFrame | None = None) -> pd.DataFrame:
    x = df.where(mask) if mask is not None else df
    return x.rank(axis=1, pct=True) * 100


def factor_scores(w: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    liquid = w["liquid"].astype(bool)
    f = {}
    trend = (
        25 * (w["close"] > w["sma_200"])
        + 20 * (w["ema_50"] > w["sma_200"])
        + 15 * (w["close"] > w["ema_50"])
        + 10 * (w["sma200_slope"] > 0)
        + 15 * (w["st_dir"] > 0)
        + 15 * ((w["adx_14"] > 20) & (w["plus_di"] > w["minus_di"]))
    ).astype(float)
    f["trend"] = trend.where(w["sma_200"].notna() | w["ema_50"].notna())
    f["momentum"] = 0.7 * w["rs_rating"] + 0.3 * _pct(w["ret_1m"])
    vol = (
        50 * (w["obv"] > w["obv_ema_20"])
        + 25 * ((w["vol_ratio"] > 1.2) & (w["chg_pct"] > 0))
        + 25 * (1 - (w["mfi_14"] - 60).abs() / 60).clip(0, 1)
    )
    f["volume"] = vol.where(w["vol_sma_20"].notna())
    f["timing"] = 100 * (1 - (w["rsi_14"] - 58).abs() / 42).clip(0, 1)
    f["sector"] = w["sector_rs_rank"]
    value_parts = [w[k] for k in ("pe_pct_sector", "pbv_pct_sector", "dy_pct_sector", "graham_pct_sector") if k in w]
    if value_parts:
        stack = np.stack([p.to_numpy(dtype=float) for p in value_parts])
        import warnings
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            val = np.nanmean(stack, axis=0) * 100
        f["value"] = pd.DataFrame(val, index=w["close"].index, columns=w["close"].columns)
    if "roe_pct_sector" in w:
        q = w["roe_pct_sector"] * 100
        if "npl" in w and w["npl"].notna().to_numpy().any():  # banks: penalise high NPL when known
            q = q - (w["npl"].clip(lower=0) * 400).clip(upper=30).fillna(0)
        f["quality"] = q.clip(0, 100)
    f["liquidity"] = _pct(w["turnover_sma_20"])
    return f


def composite(factors: dict[str, pd.DataFrame], weights: dict[str, float]) -> pd.DataFrame:
    num, den = None, None
    for k, wt in weights.items():
        if k not in factors or not wt:
            continue
        x = factors[k]
        has = x.notna().astype(float)
        num = x.fillna(0) * wt if num is None else num + x.fillna(0) * wt
        den = has * wt if den is None else den + has * wt
    return (num / den.replace(0, np.nan)).clip(0, 100)


def stance(w: dict[str, pd.DataFrame], score: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    sc = cfg.get("scoring", {})
    th = sc.get("stance_thresholds", {})
    sb, buy, red, ex = th.get("strong_buy", 75), th.get("buy", 62), th.get("reduce", 40), th.get("exit", 28)
    ob = sc.get("overbought_rsi", 80)
    ovx = sc.get("overextended_pct_above_ema50", 0.25)
    regime = w["regime"]
    liquid = w["liquid"].astype(bool)
    close = w["close"]
    up = close > w["sma_200"]
    code = pd.DataFrame(0, index=score.index, columns=score.columns, dtype=float)
    code[(score >= buy) & up & liquid] = 1
    code[(score >= sb) & up & (w["ema_50"] > w["sma_200"]) & liquid & (w["rsi_14"] < ob)] = 2
    # bear market: no new buys
    code[(code > 0) & (regime < 0)] = 0
    reduce_ = (score < red) | ((close < w["ema_50"]) & (w["st_dir"] < 0)) | (
        (w["rsi_14"] > ob) & (w["dist_ema50"] > ovx))
    code[reduce_] = -1
    exit_ = (score < ex) | ((close < w["sma_200"]) & (w["st_dir"] < 0) & (score < red))
    code[exit_] = -2
    return code.where(score.notna())


def explain(row: dict, cfg: dict) -> list[str]:
    """Human-readable reasons behind the latest stance of one stock."""
    r = []

    def g(k):
        v = row.get(k)
        return None if v is None or (isinstance(v, float) and v != v) else v

    c, s200, e50 = g("close"), g("sma_200"), g("ema_50")
    if c and s200:
        r.append(("Above" if c > s200 else "Below") + f" 200-DMA ({(c / s200 - 1) * 100:+.1f}%)")
    if e50 and s200:
        r.append("50-EMA " + ("above" if e50 > s200 else "below") + " 200-SMA (" + ("uptrend" if e50 > s200 else "downtrend") + " structure)")
    if g("st_dir") is not None:
        r.append("Supertrend " + ("bullish" if g("st_dir") > 0 else "bearish") + (f", support {g('st_line'):.1f}" if g("st_dir") > 0 and g("st_line") else ""))
    if g("adx_14") is not None:
        r.append(f"ADX {g('adx_14'):.0f} " + ("(strong trend)" if g("adx_14") > 25 else "(weak trend)" if g("adx_14") < 18 else "(moderate trend)"))
    if g("rsi_14") is not None:
        rv = g("rsi_14")
        r.append(f"RSI {rv:.0f}" + (" overbought" if rv > 75 else " oversold" if rv < 30 else ""))
    if g("rs_rating") is not None:
        r.append(f"Relative strength {g('rs_rating'):.0f}/100 vs all stocks")
    if g("vol_ratio") is not None and g("vol_ratio") > 1.8:
        r.append(f"Volume {g('vol_ratio'):.1f}× average")
    if g("sector_rs_rank") is not None:
        r.append(f"Sector strength rank {g('sector_rs_rank'):.0f}/100")
    if g("pe") is not None and g("sector_pe_median") is not None:
        r.append(f"P/E {g('pe'):.1f} vs sector median {g('sector_pe_median'):.1f}")
    elif g("eps") is not None and g("eps") <= 0:
        r.append("Loss-making (EPS ≤ 0)")
    if g("roe") is not None:
        r.append(f"ROE {g('roe') * 100:.1f}%")
    if g("div_yield") is not None and g("div_yield") > 0.03:
        r.append(f"Dividend yield {g('div_yield') * 100:.1f}%")
    if g("regime") is not None and g("regime") < 0:
        r.append("Market regime bearish — new buying paused")
    if not g("liquid"):
        r.append("Illiquid (low average turnover) — higher manipulation risk")
    return r
