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
CALLS = ["STRONG BUY", "BUY", "ACCUMULATE", "HOLD", "REDUCE", "EXIT"]


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


MODES = ("hard", "soft", "leaders", "sizing", "combined")


def _by_regime(reg: pd.DataFrame, mapping: dict, default: float) -> pd.DataFrame:
    """Map a numeric regime frame (1 bull, 0.5 recovery, 0 neutral, -1 bear) to per-regime values."""
    v = reg.to_numpy(dtype=float)
    g = lambda k: float(mapping.get(k, default))  # noqa: E731
    out = np.select([v >= 1, (v > 0) & (v < 1), v < 0], [g("bull"), g("recovery"), g("bear")], g("neutral"))
    out = np.where(np.isnan(v), g("neutral"), out)
    return pd.DataFrame(out, index=reg.index, columns=reg.columns)


def effective_regime(w: dict[str, pd.DataFrame], use_sector: bool) -> pd.DataFrame:
    """Rule 4: judge a stock by its own sector's trend first.
    Sector bull -> bull, sector bear -> bear; a neutral sector takes the better of
    sector and market (so a NEPSE recovery lifts it)."""
    mkt = w["regime"]
    if not use_sector or "sector_regime" not in w:
        return mkt
    sec = w["sector_regime"]
    out = np.where(sec >= 1, 1.0, np.where(sec <= -1, -1.0, np.maximum(sec.fillna(0), mkt)))
    out = np.where(sec.isna(), mkt, out)
    return pd.DataFrame(out, index=mkt.index, columns=mkt.columns)


def stance_policy(w: dict[str, pd.DataFrame], score: pd.DataFrame, cfg: dict, mode: str | None = None) -> dict:
    """Stance under a regime policy. Returns code, adjusted score, leader flag,
    effective regime, suggested position size, stop multiple and accumulate flag."""
    sc = cfg.get("scoring", {})
    rp = sc.get("regime_policy", {}) or {}
    mode = mode or rp.get("mode", "combined")
    th = sc.get("stance_thresholds", {})
    sb0, buy0, red, ex = th.get("strong_buy", 75), th.get("buy", 62), th.get("reduce", 40), th.get("exit", 28)
    ob = sc.get("overbought_rsi", 80)
    ovx = sc.get("overextended_pct_above_ema50", 0.25)
    mkt = w["regime"]
    liquid = w["liquid"].astype(bool)
    close = w["close"]
    up = close > w["sma_200"]
    stack = w["ema_50"] > w["sma_200"]
    base_stop = cfg.get("portfolio", {}).get("atr_stop_multiple", 3.0)

    eff = effective_regime(w, rp.get("use_sector_regime", True)) if mode == "combined" else mkt
    if mode in ("soft", "combined"):
        adj = score + _by_regime(mkt, rp.get("score_adjust", {}), 0)
        buy_th = _by_regime(eff, rp.get("buy_threshold", {}), buy0)
        sb_th = _by_regime(eff, rp.get("strong_threshold", {}), sb0)
    else:
        adj, buy_th, sb_th = score, buy0, sb0
    adj = adj.clip(0, 100)

    leader = (w["rs_rating"] >= rp.get("leader_rs_min", 85)) & up & stack & liquid & \
             (w["sector_rs_rank"] >= rp.get("leader_sector_rs_min", 70))

    code = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    code[(adj >= buy_th) & up & liquid] = 1
    code[(adj >= sb_th) & up & stack & liquid & (w["rsi_14"] < ob)] = 2
    if mode == "hard":
        code[(code > 0) & (mkt < 0)] = 0                        # original: no buys in a bear market
    elif mode == "leaders":
        code[(code > 0) & (mkt < 0) & ~leader] = 0              # bear: leaders only
    elif mode == "combined":
        code[(code > 0) & (eff < 0) & ~leader] = 0              # bear (sector-aware): leaders only
    reduce_ = (score < red) | ((close < w["ema_50"]) & (w["st_dir"] < 0)) | ((w["rsi_14"] > ob) & (w["dist_ema50"] > ovx))
    code[reduce_] = -1
    exit_ = (score < ex) | ((close < w["sma_200"]) & (w["st_dir"] < 0) & (score < red))
    code[exit_] = -2

    if mode in ("sizing", "combined"):
        size = _by_regime(eff, rp.get("position_size", {}), 1.0)
        stop = _by_regime(eff, rp.get("stop_atr", {}), base_stop)
    else:
        size = pd.DataFrame(1.0, index=score.index, columns=score.columns)
        stop = pd.DataFrame(float(base_stop), index=score.index, columns=score.columns)

    acc = pd.DataFrame(False, index=score.index, columns=score.columns)
    if mode == "combined" and rp.get("accumulate_enabled", True) and "score_value" in w:
        # "value dip": cheap versus sector peers, ROE at least the sector median,
        # profitable, NPL acceptable, the price has stopped falling (Supertrend up
        # or back above the 50-day EMA) and the stock is not already a momentum buy.
        npl_ok = w["npl"].isna() | (w["npl"] <= rp.get("accumulate_max_npl", 0.05)) if "npl" in w else True
        dip = ((w["score_value"] >= rp.get("accumulate_value_min", 75)) & (w["eps"] > 0)
               & (w["roe_pct_sector"] >= rp.get("accumulate_quality_pct_min", 0.5)) & npl_ok
               & ((w["st_dir"] > 0) | (close > w["ema_50"])) & liquid & code.isin([0, -1])).fillna(False).astype(bool)
        bear, recov = mkt < 0, (mkt > 0) & (mkt < 1)
        if rp.get("buy_rule", "value_dip") == "value_dip":
            # Calls rebuilt 19 Nov 2026 from absolute outcomes (after ~0.9% costs),
            # point-in-time data 2023-26, 60 sessions after the call:
            #   value dip in a bear market        80% made money, avg +8.7%, avg worst dip -3.7%
            #   sector leaders in a bear market   77%, +12.9% (kept)
            #   value dip in a recovery           50%, +0.7%   vs score-based buys there 29%, -4.0% (dropped)
            #   any buy in a bull market          about a random stock: 69% in 2020-22, 17% in 2025-26
            #                                     (kept, but capped at BUY)
            # Composite model 2023-26: 10.8% -> 11.8% a year (NEPSE 6.7%), max drawdown -22%.
            neutral = (mkt >= 0) & (mkt <= 0)
            if not rp.get("bear_leader_buys", True):
                code[(code > 0) & bear] = 0
            if not rp.get("recovery_score_buys", False):
                code[(code > 0) & recov] = 0
            if not rp.get("neutral_score_buys", True):
                code[(code > 0) & neutral] = 0
            # Score-based buys in neutral / bull markets are capped at BUY (size cap
            # optional): 2023-26 they made money 38-47% of the time (+1.7-3.8%),
            # while value-dip STRONG BUYs made money 79% of the time (+8.1%).
            calm = (mkt >= 0) & ~recov
            if rp.get("cap_score_buys_outside_bear", True):
                code[(code == 2) & calm] = 1
            size = size.where(~((code >= 1) & calm), np.minimum(size, rp.get("score_buy_size", 1.0)))
            code[dip & bear] = 2
            code[dip & recov] = 1
            acc = dip & (bear | recov)
            size = size.where(~(dip & bear), rp.get("value_dip_size_bear", 0.5))
            size = size.where(~(dip & recov), rp.get("value_dip_size_recovery", 0.5))
        else:  # previous behaviour: ACCUMULATE label, small size
            acc = dip & (bear | recov)
            code[acc] = 0
            size = size.where(~acc, np.minimum(size, rp.get("accumulate_size", 0.25)))

    valid = score.notna()
    return {"code": code.where(valid), "score_adj": adj.where(valid).round(1), "leader": leader.astype(float),
            "eff_regime": eff, "size": size, "stop": stop, "accumulate": acc.astype(float).where(valid, 0.0), "mode": mode}


def stance(w: dict[str, pd.DataFrame], score: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    return stance_policy(w, score, cfg)["code"]


def call_label(code, accumulate) -> str:
    if accumulate and code == 0:
        return "ACCUMULATE"
    try:
        return STANCES.get(int(code), "HOLD")
    except (TypeError, ValueError):
        return "N/A"


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
    r += regime_reasons(row, cfg)
    if not g("liquid"):
        r.append("Illiquid (low average turnover) — higher manipulation risk")
    return r


def regime_reasons(row: dict, cfg: dict) -> list[str]:
    """Explain how the market / sector regime shaped this call."""
    def g(k):
        v = row.get(k)
        return None if v is None or (isinstance(v, float) and v != v) else v
    from .market import regime_name

    rp = cfg.get("scoring", {}).get("regime_policy", {}) or {}
    mode = rp.get("mode", "combined")
    out = []
    mkt, eff, sec = g("regime"), g("eff_regime"), g("sector_regime")
    code, raw, adj = g("stance_code"), g("score"), g("score_adj")
    mname = regime_name(mkt)
    if mname == "recovery":
        out.append("NEPSE in a recovery phase (early-bottom signals): buying allowed with smaller size")
    if mode == "hard" and mname == "bear":
        out.append("Market regime bearish — new buying paused")
    if mode == "combined" and sec is not None and mkt is not None:
        if sec >= 1 and mkt < 1:
            out.append("Its sector is in its own uptrend while NEPSE is weaker")
        elif sec <= -1 and mkt >= 0:
            out.append("Its sector is in a downtrend even though NEPSE is not")
    if raw is not None and adj is not None and abs(raw - adj) >= 0.5:
        out.append(f"Score {raw:.0f} adjusted to {adj:.0f} for the {mname} market")
    bearish = (eff if mode == "combined" else mkt)
    if bearish is not None and bearish < 0 and mode in ("leaders", "combined"):
        if g("leader") and (code or 0) >= 1:
            out.append(f"Bear market: buy allowed because it is a sector-leading stock (RS {g('rs_rating') or 0:.0f})")
        elif (code or 0) == 0 and not g("accumulate"):
            out.append("Bear market: new buys are limited to sector leaders")
    if g("accumulate"):
        out.append(f"ACCUMULATE: cheap vs sector peers with solid ROE and price stabilising — build gradually "
                   f"(about {(g('pos_size') or 0.25):.0%} of a normal position)")
    elif (code or 0) >= 1 and g("pos_size") is not None and mode in ("sizing", "combined"):
        out.append(f"Suggested size {g('pos_size'):.0%} of a normal position, stop {g('stop_mult') or 3:g}× ATR")
    return out
