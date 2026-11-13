"""Write the JSON files the dashboard (docs/index.html) reads."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import store
from .config import Paths
from .engine import Analysis
from .indicators import INDICATOR_HELP
from .market import REGIME_LABEL, quadrant
from .scoring import STANCES, explain
from .utils import safe, write_json

SCREENER_FIELDS = [
    "close", "chg_pct", "volume", "turnover", "turnover_sma_20", "vol_ratio", "rsi_14", "rsi_2", "macd",
    "macd_signal", "macd_hist", "adx_14", "plus_di", "minus_di", "st_dir", "st_line", "atr_14", "atr_pct",
    "dist_sma200", "dist_ema50", "sma200_slope", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_12m", "rs_rating",
    "pct_from_52w_high", "pct_from_52w_low", "high_52w_t", "low_52w_t", "sma_20", "sma_50", "sma_200", "ema_20",
    "ema_50", "bb_pctb", "bb_width", "mfi_14", "stoch_k", "volatility_20", "sector_rs_rank", "liquid",
    "eps", "bvps", "pe", "pbv", "roe", "div_yield", "earnings_yield", "graham", "graham_discount",
    "pe_vs_sector", "pbv_vs_sector", "sector_pe_median", "sector_pbv_median", "mcap", "npl", "car", "bonus_pct",
    "score", "score_trend", "score_momentum", "score_volume", "score_timing", "score_sector", "score_value",
    "score_quality", "score_liquidity", "stance_code", "bars",
]
HISTORY_FIELDS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "e20": "ema_20",
                  "e50": "ema_50", "s200": "sma_200", "rsi": "rsi_14", "macd": "macd", "sig": "macd_signal",
                  "hist": "macd_hist", "st": "st_line", "std": "st_dir", "bbu": "bb_up", "bbl": "bb_low",
                  "score": "score", "stance": "stance_code"}


def _round_list(s: pd.Series, nd=2):
    return [None if (v != v or v is None) else round(float(v), nd) for v in s.to_numpy(dtype=float)]


def export_all(a: Analysis, paths: Paths, cfg: dict, presets: list[dict], hits: dict, strategies: list[dict],
               tracking: dict, demo: bool = False) -> None:
    out = paths.out
    snap = a.latest()
    last = a.dates[-1]
    fund = store.read_fundamentals(paths)
    fmeta = fund.set_index("symbol")[["eps_period", "source", "as_of"]].to_dict("index") if len(fund) else {}
    hit_map: dict[str, list[str]] = {}
    for pid, syms in hits.items():
        for s in syms:
            hit_map.setdefault(s, []).append(pid)

    # ---------------- screener rows
    rows = []
    for sym, r in snap.iterrows():
        rec = {"symbol": sym, "name": r.get("name", ""), "sector": r.get("sector", "Others")}
        for f in SCREENER_FIELDS:
            if f in r.index:
                rec[f] = safe(r[f], 4)
        code = r.get("stance_code")
        rec["stance"] = STANCES.get(int(code), "") if code == code and code is not None else "N/A"
        rec["reasons"] = explain(r.to_dict(), cfg)
        lt = r.get("last_traded")
        rec["last_traded"] = lt.strftime("%Y-%m-%d") if isinstance(lt, pd.Timestamp) else None
        rec["stale"] = bool(isinstance(lt, pd.Timestamp) and (last - lt).days > 7)
        rec["screens"] = hit_map.get(sym, [])
        sp = a.w["close"][sym].iloc[-40:]
        rec["spark"] = _round_list(sp, 2)
        fm = fmeta.get(sym, {})
        rec["eps_period"] = fm.get("eps_period") if isinstance(fm.get("eps_period"), str) else None
        rec["fund_source"] = fm.get("source") if isinstance(fm.get("source"), str) else None
        rows.append(rec)
    write_json(out / "screener.json", {"date": last.strftime("%Y-%m-%d"), "rows": rows})

    # ---------------- presets
    write_json(out / "presets.json", [{**p, "hits": hits.get(p["id"], [])} for p in presets])

    # ---------------- per-stock history
    n = int(cfg.get("export", {}).get("history_bars", 500))
    dates = a.dates[-n:]
    hist_dir = out / "history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for sym in a.symbols:
        traded = a.w["traded"][sym].loc[dates] > 0
        d = dates[traded.to_numpy()]
        if len(d) < 5:
            continue
        obj = {"symbol": sym, "d": [x.strftime("%Y-%m-%d") for x in d]}
        for k, f in HISTORY_FIELDS.items():
            if f in a.w:
                obj[k] = _round_list(a.w[f][sym].loc[d], 0 if k == "v" else 2)
        write_json(hist_dir / f"{sym}.json", obj)
        keep.add(f"{sym}.json")
    for f in hist_dir.glob("*.json"):
        if f.name not in keep:
            f.unlink()

    # ---------------- market
    m = a.market.iloc[-750:]
    lr = a.market.iloc[-1]
    prev = a.market.iloc[-2] if len(a.market) > 1 else lr
    liquid_snap = snap[snap["traded"] > 0] if "traded" in snap else snap
    def top(col, asc, k=10, extra="chg_pct"):
        t = liquid_snap.dropna(subset=[col]).sort_values(col, ascending=asc).head(k)
        return [{"symbol": s, "sector": r["sector"], "close": safe(r["close"]), "chg_pct": safe(r["chg_pct"]),
                 "turnover": safe(r.get("turnover")), "value": safe(r[col])} for s, r in t.iterrows()]
    market = {
        "date": last.strftime("%Y-%m-%d"), "index_source": a.index_source,
        "index": safe(lr["index"]), "index_chg": safe(lr["index"] / prev["index"] - 1),
        "regime": int(lr["regime"]), "regime_label": REGIME_LABEL[int(lr["regime"])],
        "breadth_50": safe(lr.get("breadth_50")), "breadth_200": safe(lr.get("breadth_200")),
        "advances": safe(lr.get("advances")), "declines": safe(lr.get("declines")),
        "new_highs": safe(lr.get("new_highs")), "new_lows": safe(lr.get("new_lows")),
        "ret_1m": safe(lr.get("mkt_ret_1m")), "ret_3m": safe(lr.get("mkt_ret_3m")), "ret_6m": safe(lr.get("mkt_ret_6m")),
        "turnover": safe(float(snap["turnover"].sum())) if "turnover" in snap else None,
        "stance_counts": {STANCES[c]: int((snap["stance_code"] == c).sum()) for c in STANCES},
        "series": {"d": [x.strftime("%Y-%m-%d") for x in m.index], "index": _round_list(m["index"]),
                   "fast": _round_list(m["ema_fast"]), "slow": _round_list(m["sma_slow"]),
                   "regime": [int(x) for x in m["regime"]], "b50": _round_list(m["breadth_50"], 3),
                   "b200": _round_list(m["breadth_200"], 3)},
        "gainers": top("chg_pct", False), "losers": top("chg_pct", True), "turnover_leaders": top("turnover", False),
        "volume_spikes": top("vol_ratio", False),
    }
    write_json(out / "market.json", market)

    # ---------------- sectors
    secs = []
    for sec, f in a.sectors.items():
        lr_ = f.iloc[-1]
        members = snap[snap["sector"] == sec]
        weekly = f.iloc[-60::5]
        s250 = f["index"].iloc[-250:]
        secs.append({
            "sector": sec, "n": int(len(members)), "n_liquid": int(members["liquid"].sum()) if "liquid" in members else None,
            "ret_1w": safe(lr_["ret_1w"]), "ret_1m": safe(lr_["ret_1m"]), "ret_3m": safe(lr_["ret_3m"]),
            "ret_6m": safe(lr_["ret_6m"]), "ret_12m": safe(lr_["ret_12m"]), "rs_rank": safe(lr_["rs_rank"]),
            "rs_ratio": safe(lr_["rs_ratio"]), "rs_mom": safe(lr_["rs_mom"]),
            "quadrant": quadrant(lr_["rs_ratio"], lr_["rs_mom"]), "above_200": safe(lr_["above_200"]),
            "median_pe": safe(members["pe"].median()) if "pe" in members else None,
            "median_pbv": safe(members["pbv"].median()) if "pbv" in members else None,
            "median_roe": safe(members["roe"].median()) if "roe" in members else None,
            "median_div_yield": safe(members["div_yield"].median()) if "div_yield" in members else None,
            "avg_score": safe(members["score"].mean()),
            "buys": int((members["stance_code"] >= 1).sum()), "exits": int((members["stance_code"] <= -1).sum()),
            "leaders": members.sort_values("score", ascending=False).head(5).index.tolist(),
            "series": {"d": [x.strftime("%Y-%m-%d") for x in s250.index], "v": _round_list(s250 / s250.iloc[0] * 100)},
            "tail": [{"x": safe(r["rs_ratio"]), "y": safe(r["rs_mom"])} for _, r in weekly.iterrows()],
        })
    secs.sort(key=lambda s: -(s["rs_rank"] or 0))
    write_json(out / "sectors.json", secs)

    # ---------------- strategies, tracking, fields, meta
    write_json(out / "strategies.json", strategies)
    write_json(out / "tracking.json", tracking)
    write_json(out / "fields.json", INDICATOR_HELP)
    status = {}
    try:
        import json
        status = json.loads(paths.status.read_text()) if paths.status.exists() else {}
    except Exception:
        pass
    write_json(out / "meta.json", {
        "generated": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC"), "last_trade_date": last.strftime("%Y-%m-%d"),
        "stocks": len(a.symbols), "scored": int(snap["score"].notna().sum()),
        "liquid": int(snap["liquid"].sum()) if "liquid" in snap else None,
        "fundamentals_mode": a.fundamentals_mode, "fundamentals_count": int(len(fund)),
        "index_source": a.index_source, "warnings": a.warnings, "sources": status, "demo": demo,
        "settings": {"costs": cfg.get("costs"), "portfolio": cfg.get("portfolio"),
                     "scoring": cfg.get("scoring"), "tracking": cfg.get("tracking")},
    })
