"""Portfolio advisor: position-aware BUY MORE / HOLD / REDUCE / EXIT calls.

Combines the stock's model stance with things only YOUR position knows:
your cost (WACC), how long you've held (capital-gains tax rate), a trailing
stop measured from the highest close since you bought, position size and
sector concentration, and the overall market regime."""
from __future__ import annotations

import io
from datetime import date

import numpy as np
import pandas as pd

from .costs import Costs
from .engine import Analysis
from .scoring import STANCES, explain

ACTIONS = ["EXIT", "REDUCE", "HOLD", "ADD"]


def load_portfolio(source: str) -> pd.DataFrame:
    """CSV with columns: symbol, quantity, wacc[, buy_date]  (MeroShare 'My WACC' export works)."""
    if "\n" in source or "," in source and not source.strip().endswith(".csv"):
        df = pd.read_csv(io.StringIO(source))
    else:
        df = pd.read_csv(source)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    alias = {"scrip": "symbol", "script": "symbol", "qty": "quantity", "current_balance": "quantity",
             "balance": "quantity", "wacc_rate": "wacc", "rate": "wacc", "avg_cost": "wacc", "cost": "wacc",
             "purchase_date": "buy_date", "date": "buy_date"}
    df = df.rename(columns={k: v for k, v in alias.items() if k in df.columns and v not in df.columns})
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["quantity"] = pd.to_numeric(df["quantity"].astype(str).str.replace(",", ""), errors="coerce")
    df["wacc"] = pd.to_numeric(df.get("wacc", np.nan), errors="coerce")
    if "buy_date" in df:
        df["buy_date"] = pd.to_datetime(df["buy_date"], errors="coerce")
    else:
        df["buy_date"] = pd.NaT
    df = df.dropna(subset=["quantity"])
    df = df[df["quantity"] > 0]
    # combine duplicate lots into one weighted position (earliest buy date kept)
    agg = df.groupby("symbol").apply(lambda g: pd.Series({
        "quantity": g["quantity"].sum(),
        "wacc": np.average(g["wacc"].fillna(0), weights=g["quantity"]) if g["wacc"].notna().any() else np.nan,
        "buy_date": g["buy_date"].min()}), include_groups=False).reset_index()
    return agg


def analyze(a: Analysis, holdings: pd.DataFrame, cfg: dict) -> dict:
    pc = cfg.get("portfolio", {})
    costs = Costs(cfg)
    k = pc.get("atr_stop_multiple", 3.0)
    hard = pc.get("hard_stop_pct", 0.15)
    max_w = pc.get("max_position_weight", 0.20)
    max_sw = pc.get("max_sector_weight", 0.40)
    red_frac = pc.get("reduce_fraction", 0.5)
    tax_win = pc.get("tax_wait_window_days", 30)
    lt_days = cfg.get("costs", {}).get("long_term_days", 365)
    snap = a.latest()
    today = a.dates[-1]
    regime = int(a.market["regime"].iloc[-1])
    close_w = a.w["close"]
    positions, unknown = [], []
    total_value = 0.0
    for h in holdings.to_dict("records"):
        sym = h["symbol"]
        if sym not in snap.index:
            unknown.append(sym)
            continue
        r = snap.loc[sym]
        ltp = float(r["close"])
        qty = float(h["quantity"])
        value = qty * ltp
        total_value += value
        positions.append({"h": h, "r": r, "ltp": ltp, "qty": qty, "value": value})
    sector_val: dict[str, float] = {}
    for p in positions:
        sector_val[p["r"]["sector"]] = sector_val.get(p["r"]["sector"], 0) + p["value"]
    out = []
    for p in positions:
        h, r, ltp, qty, value = p["h"], p["r"], p["ltp"], p["qty"], p["value"]
        sym = h["symbol"]
        wacc = float(h["wacc"]) if h.get("wacc") == h.get("wacc") and h.get("wacc") else ltp
        bd = h.get("buy_date")
        bd = pd.Timestamp(bd) if bd is not None and bd == bd else None
        cost = qty * wacc
        weight = value / total_value if total_value else 0
        sw = sector_val.get(r["sector"], 0) / total_value if total_value else 0
        # trailing stop from the highest close since purchase (or last 60 sessions)
        code = int(r["stance_code"]) if r.get("stance_code") == r.get("stance_code") else 0
        series = close_w[sym].dropna()
        since = series[series.index >= bd] if bd is not None else series.iloc[-60:]
        since = since.iloc[-120:]  # trailing stop looks back at most ~6 months
        peak = max(float(since.max()), ltp) if len(since) else ltp
        recent_peak = max(float(series.iloc[-20:].max()), ltp)
        atr_v = float(r.get("atr_14") or 0)
        stop = peak - k * atr_v if atr_v else None
        basis = f"{k}× ATR below highest close {peak:.1f}"
        reset = False
        if stop is not None and ltp < stop and code >= 1:
            # stop was breached in the past but the trend has recovered: reset to the recent swing
            stop, basis, reset = recent_peak - k * atr_v, f"{k}× ATR below 20-day high {recent_peak:.1f}", True
        if r.get("st_dir", 0) > 0 and r.get("st_line") == r.get("st_line") and r.get("st_line"):
            if stop is None or r["st_line"] > stop:
                stop, basis = float(r["st_line"]), "Supertrend support"
        hold_days = (today - bd).days if bd is not None else None
        # MeroShare WACC already includes buy-side commission & SEBON fee
        br = costs.sell_breakdown(qty, ltp, cost, hold_days if hold_days is not None else 0)
        days_to_lt = (lt_days + 1 - hold_days) if hold_days is not None and hold_days <= lt_days else 0
        reasons = explain(r.to_dict(), cfg)
        action, detail = "HOLD", "Trend and score do not justify changing the position."
        sell_qty = 0
        if stop is not None and ltp < stop:
            action, detail, sell_qty = "EXIT", f"Closed below trailing stop {stop:.1f} ({basis}).", qty
        elif code == -2:
            action, detail, sell_qty = "EXIT", "Model rates the stock EXIT: broken trend and weak score.", qty
        elif ltp < wacc * (1 - hard) and code <= 0:
            action, detail, sell_qty = "EXIT", f"Loss beyond {hard:.0%} of your cost with no supportive trend — cut the loss.", qty
        elif code == -1:
            over = (r.get("rsi_14") or 0) > cfg.get("scoring", {}).get("overbought_rsi", 80)
            sell_qty = int(qty * red_frac)
            action = "REDUCE"
            detail = ("Overextended rally — book partial profit." if over and ltp > wacc
                      else "Trend weakening — reduce exposure, keep the rest with the stop.")
        elif weight > max_w:
            action = "REDUCE"
            sell_qty = int(qty * (1 - max_w / weight))
            detail = f"Position is {weight:.0%} of the portfolio (limit {max_w:.0%}) — trim to control risk."
        elif code >= 1 and regime >= 0 and weight < max_w * 0.8 and sw < max_sw:
            action = "ADD"
            detail = "Model rates BUY" + (" (STRONG)" if code == 2 else "") + " and the trend is up — adding on dips is reasonable."
        notes = []
        if action != "EXIT" and br["gain_before_tax"] > 0 and 0 < days_to_lt <= tax_win:
            saved = br["gain_before_tax"] * (costs.cgt_short - costs.cgt_long)
            notes.append(f"Hold {days_to_lt} more days for long-term CGT ({costs.cgt_long:.2%} vs {costs.cgt_short:.0%}) — saves ≈ Rs {saved:,.0f}.")
        elif action == "EXIT" and br["gain_before_tax"] > 0 and 0 < days_to_lt <= 10 and "stop" not in detail:
            notes.append(f"Only {days_to_lt} days to long-term CGT; if the chart allows, waiting saves tax.")
        if sw > max_sw:
            notes.append(f"Sector {r['sector']} is {sw:.0%} of the portfolio (limit {max_sw:.0%}).")
        if not r.get("liquid"):
            notes.append("Low liquidity — exits may take several sessions.")
        if regime < 0 and action == "HOLD":
            notes.append("Bear market regime — tighten stops, avoid averaging down.")
        if reset:
            notes.append("The older trailing stop was breached earlier, but the trend has recovered — stop reset to the recent swing.")
        if bd is None:
            notes.append("Add buy_date to get the exact CGT rate and a precise trailing stop.")
        out.append({
            "symbol": sym, "name": r.get("name", ""), "sector": r["sector"], "qty": qty, "wacc": round(wacc, 2),
            "ltp": round(ltp, 2), "cost": round(cost, 0), "value": round(value, 0),
            "pnl": round(value - cost, 0), "pnl_pct": value / cost - 1 if cost else None, "weight": weight,
            "stance": STANCES.get(code, "HOLD"), "stance_code": code, "score": r.get("score"),
            "action": action, "action_detail": detail, "sell_qty": int(sell_qty),
            "stop": round(stop, 2) if stop else None, "stop_basis": basis,
            "hold_days": hold_days, "cgt_rate": br["cgt_rate"], "days_to_long_term": days_to_lt,
            "net_if_sold": round(br["net_proceeds"], 0), "net_profit_if_sold": round(br["net_profit"], 0),
            "rsi_14": r.get("rsi_14"), "rs_rating": r.get("rs_rating"), "reasons": reasons, "notes": notes,
        })
    order = {"EXIT": 0, "REDUCE": 1, "ADD": 2, "HOLD": 3}
    out.sort(key=lambda x: (order[x["action"]], -x["value"]))
    held = {p["symbol"] for p in out}
    cands = snap[(snap["stance_code"] >= 1) & (~snap.index.isin(held))].sort_values("score", ascending=False)
    over_sectors = {s for s, v in sector_val.items() if total_value and v / total_value > max_sw}
    cands = cands[~cands["sector"].isin(over_sectors)].head(8)
    total_cost = sum(x["cost"] for x in out)
    summary = {
        "date": today.strftime("%Y-%m-%d"), "value": total_value, "cost": total_cost,
        "pnl": total_value - total_cost, "pnl_pct": total_value / total_cost - 1 if total_cost else None,
        "positions": len(out), "unknown_symbols": unknown, "regime": regime,
        "regime_advice": ("Bull market: stay invested, let winners run with trailing stops." if regime > 0 else
                          "Sideways market: be selective, book profits faster, keep some cash." if regime == 0 else
                          f"Bear market: protect capital — aim for ~{pc.get('bear_market_cash_target', 0.4):.0%} cash, no averaging down."),
        "sector_weights": {s: v / total_value for s, v in sorted(sector_val.items(), key=lambda x: -x[1])} if total_value else {},
        "actions": {a_: sum(1 for x in out if x["action"] == a_) for a_ in ACTIONS},
    }
    return {"summary": summary, "positions": out,
            "candidates": [{"symbol": s, "name": c["name"], "sector": c["sector"], "score": c["score"],
                            "stance": STANCES.get(int(c["stance_code"]), ""), "close": c["close"]} for s, c in cands.iterrows()]}
