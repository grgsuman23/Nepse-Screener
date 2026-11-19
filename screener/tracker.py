"""Recommendation tracker — "did the calls actually work?"

* Every run appends today's calls to permanent ledgers (CSV = the database):
    data/tracking/recommendations.csv   model stance for every stock, every day
    data/tracking/strategy_signals.csv  each strategy's entries / exits / picks
    data/tracking/portfolio_actions.csv your portfolio's EXIT/REDUCE/HOLD/ADD calls
* Each call is scored after 5 / 20 / 60 trading days against the real price
  and against the NEPSE index (excess return). Results are written to
  data/tracking/outcomes.csv and summarised for the dashboard.
* A SIMULATED track record is also rebuilt from price history so you have
  statistics from day one; it is always reported separately from LIVE calls.

Hit definitions
  BUY / STRONG BUY / ADD / ENTRY / PICK  -> hit if the stock beat the index
  REDUCE / EXIT                          -> hit if the stock lagged the index
  HOLD                                   -> no hit rate; average returns only
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Paths
from .engine import Analysis
from .scoring import STANCES, call_label
from .utils import log

REC_COLS = ["date", "symbol", "sector", "stance_code", "stance", "score", "close", "regime", "source", "model_version"]
SIG_COLS = ["date", "strategy", "symbol", "signal", "close", "source"]
PF_COLS = ["date", "symbol", "action", "close", "source"]


def _append(path, df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if path.exists():
        old = pd.read_csv(path, dtype={"symbol": str})
        df = pd.concat([old, df], ignore_index=True)
    df = df.drop_duplicates(keys, keep="last").sort_values(keys)
    df.to_csv(path, index=False)
    return df


def log_recommendations(a: Analysis, paths: Paths, cfg: dict) -> int:
    snap = a.latest()
    snap = snap[snap["score"].notna()]
    d = a.dates[-1].strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "date": d, "symbol": snap.index, "sector": snap["sector"].values,
        "stance_code": snap["stance_code"].astype(int).values,
        "stance": [call_label(c, acc) for c, acc in zip(snap["stance_code"], snap.get("accumulate", pd.Series(0, index=snap.index)))],
        "score": snap["score"].round(1).values, "close": snap["close"].round(2).values,
        "regime": float(a.market["regime"].iloc[-1]), "source": "live",
        "model_version": cfg.get("tracking", {}).get("model_version", "1.0"),
    })
    _append(paths.tracking / "recommendations.csv", df[REC_COLS], ["date", "symbol"])
    return len(df)


def log_strategy_signals(a: Analysis, paths: Paths, current: dict[str, dict]) -> int:
    d = a.dates[-1]
    close = a.w["close"].loc[d]
    rows = []
    for sid, cur in current.items():
        for kind, key in (("ENTRY", "entries"), ("EXIT", "exits"), ("PICK", "picks")):
            for s in cur.get(key, []) or []:
                rows.append({"date": d.strftime("%Y-%m-%d"), "strategy": sid, "symbol": s, "signal": kind,
                             "close": round(float(close.get(s, np.nan)), 2), "source": "live"})
    if rows:
        _append(paths.tracking / "strategy_signals.csv", pd.DataFrame(rows)[SIG_COLS], ["date", "strategy", "symbol", "signal"])
    return len(rows)


def log_portfolio_actions(a: Analysis, paths: Paths, pf: dict) -> int:
    d = a.dates[-1].strftime("%Y-%m-%d")
    rows = [{"date": d, "symbol": p["symbol"], "action": p["action"], "close": p["ltp"], "source": "live"}
            for p in pf.get("positions", [])]
    if rows:
        _append(paths.tracking / "portfolio_actions.csv", pd.DataFrame(rows)[PF_COLS], ["date", "symbol"])
    return len(rows)


# ---------------------------------------------------------------- evaluation
def forward_returns(a: Analysis, calls: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Add ret_{h}, bench_{h}, excess_{h} columns (NaN while not yet resolved)."""
    if calls.empty:
        return calls
    close = a.w["close"]
    idx = a.market["index"].reindex(close.index).ffill()
    dates = close.index
    pos = dates.get_indexer(pd.to_datetime(calls["date"]))
    # calls made on non-trading dates map to the previous session
    miss = pos < 0
    if miss.any():
        pos[miss] = dates.searchsorted(pd.to_datetime(calls["date"][miss]), side="right") - 1
    col = close.columns.get_indexer(calls["symbol"])
    C = close.to_numpy(dtype=float)
    Hh = a.w["high"].reindex(columns=close.columns).to_numpy(dtype=float) if "high" in a.w else C
    Ll = a.w["low"].reindex(columns=close.columns).to_numpy(dtype=float) if "low" in a.w else C
    I = idx.to_numpy(dtype=float)
    out = calls.copy()
    ok = (pos >= 0) & (col >= 0)
    base = np.full(len(calls), np.nan)
    base[ok] = C[pos[ok], col[ok]]
    ibase = np.full(len(calls), np.nan)
    ibase[pos >= 0] = I[pos[pos >= 0]]
    for h in horizons:
        fp = pos + h
        valid = ok & (fp < len(dates))
        r = np.full(len(calls), np.nan)
        b = np.full(len(calls), np.nan)
        r[valid] = C[fp[valid], col[valid]] / base[valid] - 1
        b[valid] = I[fp[valid]] / ibase[valid] - 1
        # best and worst point reached while holding (from the next session to day h)
        mx = np.full(len(calls), np.nan)
        mn = np.full(len(calls), np.nan)
        for k in np.where(valid)[0]:
            p0, j = pos[k], col[k]
            hi = np.nanmax(Hh[p0 + 1: p0 + h + 1, j]) if h > 0 else np.nan
            lo = np.nanmin(Ll[p0 + 1: p0 + h + 1, j]) if h > 0 else np.nan
            mx[k], mn[k] = hi / base[k] - 1, lo / base[k] - 1
        out[f"ret_{h}"] = r
        out[f"bench_{h}"] = b
        out[f"excess_{h}"] = r - b
        out[f"maxgain_{h}"] = mx
        out[f"maxloss_{h}"] = mn
    return out


def _direction(label: str) -> int:
    lab = str(label).upper()
    if lab in ("STRONG BUY", "BUY", "ACCUMULATE", "ADD", "ENTRY", "PICK"):
        return 1
    if lab in ("REDUCE", "EXIT"):
        return -1
    return 0


COST = 0.009


def summarize(out: pd.DataFrame, label_col: str, horizons: list[int], cost: float | None = None) -> list[dict]:
    """Per call type and horizon.

    Absolute (what you can actually earn — NEPSE has no index fund or futures):
      success_h     buy calls: share that made money after round-trip costs;
                    reduce / exit calls: share where the price then fell
      avg_net_h     average return after costs (buy) or average move avoided (sell)
      avg_maxgain_h / avg_maxloss_h   best / worst point reached while holding
      worst5_h / best95_h             5th / 95th percentile return
    Relative (secondary): hit_h / avg_excess_h versus the NEPSE index."""
    cost = COST if cost is None else cost
    rows = []
    if out.empty:
        return rows
    for lab, g in out.groupby(label_col):
        d = _direction(lab)
        rec = {"label": lab, "direction": d, "calls": int(len(g))}
        for h in horizons:
            ex = g[f"excess_{h}"].dropna()
            rr = g[f"ret_{h}"].dropna()
            rec[f"n_{h}"] = int(len(ex))
            rec[f"avg_ret_{h}"] = float(rr.mean()) if len(rr) else None
            rec[f"med_ret_{h}"] = float(rr.median()) if len(rr) else None
            rec[f"avg_excess_{h}"] = float(ex.mean()) if len(ex) else None
            net = rr - cost if d > 0 else rr
            rec[f"success_{h}"] = (float((net > 0).mean()) if d > 0 else float((rr < 0).mean())) if (d != 0 and len(rr)) else None
            rec[f"avg_net_{h}"] = float(net.mean()) if len(rr) else None
            mg, ml = g.get(f"maxgain_{h}"), g.get(f"maxloss_{h}")
            rec[f"avg_maxgain_{h}"] = float(mg.dropna().mean()) if mg is not None and mg.notna().any() else None
            rec[f"avg_maxloss_{h}"] = float(ml.dropna().mean()) if ml is not None and ml.notna().any() else None
            rec[f"worst5_{h}"] = float(net.quantile(0.05)) if len(rr) >= 20 else None
            rec[f"best95_{h}"] = float(net.quantile(0.95)) if len(rr) >= 20 else None
            if d != 0 and len(ex):
                rec[f"hit_{h}"] = float(((ex > 0) if d > 0 else (ex < 0)).mean())
                rec[f"abs_hit_{h}"] = float(((rr > 0) if d > 0 else (rr < 0)).mean())
            else:
                rec[f"hit_{h}"] = None
                rec[f"abs_hit_{h}"] = None
        rows.append(rec)
    order = {"STRONG BUY": 0, "BUY": 1, "ADD": 1, "PICK": 1, "ENTRY": 1, "ACCUMULATE": 2, "HOLD": 3, "REDUCE": 4, "EXIT": 5}
    rows.sort(key=lambda r: order.get(str(r["label"]).upper(), 9))
    return rows


def information_coefficient(out: pd.DataFrame, h: int) -> dict:
    """Average rank correlation between score and forward return, per date."""
    col = f"ret_{h}"
    if out.empty or col not in out:
        return {}
    ics = []
    for d, g in out.dropna(subset=[col, "score"]).groupby("date"):
        if len(g) >= 15:
            ics.append(g["score"].rank().corr(g[col].rank()))
    ics = pd.Series(ics).dropna()
    if ics.empty:
        return {}
    # top-minus-bottom quintile spread
    q = out.dropna(subset=[col, "score"]).copy()
    q["q"] = q.groupby("date")["score"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) if len(s) >= 15 else np.nan)
    top = q[q["q"] == 4][col].mean()
    bot = q[q["q"] == 0][col].mean()
    return {"horizon": h, "mean_ic": float(ics.mean()), "ic_t_stat": float(ics.mean() / ics.std() * np.sqrt(len(ics))) if ics.std() > 0 else None,
            "positive_ic_share": float((ics > 0).mean()), "dates": int(len(ics)),
            "top_quintile_ret": float(top) if top == top else None, "bottom_quintile_ret": float(bot) if bot == bot else None}


def by_group(out: pd.DataFrame, group: str, h: int) -> list[dict]:
    rows = []
    if out.empty:
        return rows
    o = out[out["stance_code"] >= 1].dropna(subset=[f"excess_{h}"])
    for k, g in o.groupby(group):
        rows.append({group: k if not isinstance(k, (np.integer,)) else int(k), "calls": int(len(g)),
                     "success": float((g[f"ret_{h}"] - COST > 0).mean()), "avg_net": float((g[f"ret_{h}"] - COST).mean()),
                     "hit": float((g[f"excess_{h}"] > 0).mean()), "avg_excess": float(g[f"excess_{h}"].mean())})
    return sorted(rows, key=lambda r: -r["calls"])


def rolling_hit(out: pd.DataFrame, h: int) -> list[dict]:
    o = out[out["stance_code"] >= 1].dropna(subset=[f"excess_{h}"]).copy()
    if o.empty:
        return []
    o["month"] = pd.to_datetime(o["date"]).dt.to_period("M").astype(str)
    rows = []
    for m, g in o.groupby("month"):
        ex, r = g[f"excess_{h}"], g[f"ret_{h}"] - COST
        rows.append({"month": m, "success": float((r > 0).mean()), "avg_net": float(r.mean()),
                     "hit": float((ex > 0).mean()), "avg_excess": float(ex.mean()), "calls": int(len(g))})
    return rows


def simulated_calls(a: Analysis, days: int, step: int = 5) -> pd.DataFrame:
    """Rebuild what the model WOULD have said on past dates (every `step` sessions)."""
    sc, code = a.w["score"], a.w["stance_code"]
    accw = a.w.get("accumulate")
    dates = a.dates[-days:][::step]
    frames = []
    for d in dates:
        s, c = sc.loc[d], code.loc[d]
        m = s.notna() & c.notna()
        if not m.any():
            continue
        acc = accw.loc[d][m].values if accw is not None else np.zeros(int(m.sum()))
        frames.append(pd.DataFrame({"date": d.strftime("%Y-%m-%d"), "symbol": s.index[m], "score": s[m].values,
                                    "stance_code": c[m].astype(int).values, "accumulate": acc}))
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "score", "stance_code", "stance", "sector", "regime"])
    df = pd.concat(frames, ignore_index=True)
    df["stance"] = [call_label(c, x) for c, x in zip(df["stance_code"], df["accumulate"])]
    df["sector"] = df["symbol"].map(a.sector_of)
    reg = a.market["regime"]
    df["regime"] = pd.to_datetime(df["date"]).map(reg).astype(float)
    return df


def evaluate_all(a: Analysis, paths: Paths, cfg: dict) -> dict:
    global COST
    COST = float(cfg.get("tracking", {}).get("round_trip_cost", COST))
    hz = cfg.get("tracking", {}).get("horizons", [5, 20, 60])
    main_h = hz[1] if len(hz) > 1 else hz[0]
    res: dict = {"horizons": hz, "main_horizon": main_h, "round_trip_cost": COST}
    # ---- live model calls
    f = paths.tracking / "recommendations.csv"
    live = pd.read_csv(f, dtype={"symbol": str}) if f.exists() else pd.DataFrame(columns=REC_COLS)
    live_out = forward_returns(a, live, hz) if len(live) else live
    if len(live_out):
        live_out.to_csv(paths.tracking / "outcomes.csv", index=False)
    res["live"] = {
        "first_date": live["date"].min() if len(live) else None, "last_date": live["date"].max() if len(live) else None,
        "days_logged": int(live["date"].nunique()) if len(live) else 0, "calls": int(len(live)),
        "by_stance": summarize(live_out, "stance", hz) if len(live_out) else [],
        "ic": information_coefficient(live_out, main_h) if len(live_out) else {},
        "by_sector": by_group(live_out, "sector", main_h) if len(live_out) else [],
        "by_regime": by_group(live_out, "regime", main_h) if len(live_out) else [],
        "monthly": rolling_hit(live_out, main_h) if len(live_out) else [],
    }
    recent = live_out[live_out["stance_code"] != 0].sort_values("date", ascending=False).head(300) if len(live_out) else live_out
    res["live"]["recent"] = recent[[c for c in ["date", "symbol", "sector", "stance", "score", "close"] +
                                    [f"ret_{h}" for h in hz] + [f"excess_{h}" for h in hz] + [f"maxgain_{h}" for h in hz] + [f"maxloss_{h}" for h in hz] if c in recent]].to_dict("records") if len(recent) else []
    # ---- simulated history
    sim = simulated_calls(a, int(cfg.get("tracking", {}).get("simulated_history_days", 750)))
    sim_out = forward_returns(a, sim, hz) if len(sim) else sim
    res["simulated"] = {
        "first_date": sim["date"].min() if len(sim) else None, "last_date": sim["date"].max() if len(sim) else None,
        "calls": int(len(sim)), "sampling": "every 5th session",
        "by_stance": summarize(sim_out, "stance", hz) if len(sim_out) else [],
        "ic": information_coefficient(sim_out, main_h) if len(sim_out) else {},
        "by_sector": by_group(sim_out, "sector", main_h) if len(sim_out) else [],
        "by_regime": by_group(sim_out, "regime", main_h) if len(sim_out) else [],
        "monthly": rolling_hit(sim_out, main_h) if len(sim_out) else [],
    }
    # ---- strategy signals (live)
    f = paths.tracking / "strategy_signals.csv"
    sig = pd.read_csv(f, dtype={"symbol": str}) if f.exists() else pd.DataFrame(columns=SIG_COLS)
    strat = {}
    if len(sig):
        sig_out = forward_returns(a, sig, hz)
        for sid, g in sig_out.groupby("strategy"):
            strat[sid] = {"signals": int(len(g)), "by_signal": summarize(g, "signal", hz)}
    res["strategies_live"] = strat
    # ---- portfolio actions (live)
    f = paths.tracking / "portfolio_actions.csv"
    if f.exists():
        pfa = pd.read_csv(f, dtype={"symbol": str})
        res["portfolio_live"] = summarize(forward_returns(a, pfa, hz), "action", hz)
    log.info("Tracker: %d live calls, %d simulated calls evaluated", len(live), len(sim))
    return res
