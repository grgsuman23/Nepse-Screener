"""Bonus / right-share price adjustment.

NEPSE prices drop on the book-closure (ex) date when bonus or right shares
are issued. Unadjusted, that drop looks like a crash and breaks moving
averages, stops and backtests. NEPSE's daily price band is ±10%, so any
one-day fall beyond that can only be a corporate-action adjustment.
When NEPSE supplies the exchange-adjusted previous close we use the exact
factor; otherwise we estimate it from the gap."""
from __future__ import annotations

import pandas as pd


def _rescale_before(df: pd.DataFrame, idx: int, factor: float) -> pd.DataFrame:
    df = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    mask = df.index < idx
    for c in ("open", "high", "low", "close"):
        df.loc[mask, c] = df.loc[mask, c] * factor
    if "volume" in df:
        df.loc[mask, "volume"] = df.loc[mask, "volume"] / factor
    return df


def auto_adjust(df: pd.DataFrame, symbol: str, threshold: float = -0.11) -> tuple[pd.DataFrame, list[dict]]:
    """Detect and back-adjust corporate actions in an unadjusted series."""
    if df is None or len(df) < 2:
        return df, []
    df = df.sort_values("date").reset_index(drop=True)
    events = []
    has_prev = "prev_close" in df.columns
    # walk from newest to oldest so factors compound correctly
    for i in range(len(df) - 1, 0, -1):
        prev_c = df.at[i - 1, "close"]
        if not prev_c or prev_c != prev_c:
            continue
        factor = None
        if has_prev:
            pc = df.at[i, "prev_close"]
            if pc == pc and pc and abs(pc / prev_c - 1) > 0.02 and pc < prev_c:
                factor = pc / prev_c
        if factor is None:
            gap = df.at[i, "open"] / prev_c - 1 if df.at[i, "open"] == df.at[i, "open"] else df.at[i, "close"] / prev_c - 1
            if gap < threshold:
                factor = df.at[i, "close"] / prev_c
        if factor is not None and 0.2 < factor < 0.99:
            df = _rescale_before(df, i, factor)
            events.append({"symbol": symbol, "date": pd.Timestamp(df.at[i, "date"]).strftime("%Y-%m-%d"),
                           "factor": round(factor, 5), "method": "prev_close" if has_prev else "gap"})
    return df, events


def append_day(hist: pd.DataFrame, row: dict, symbol: str) -> tuple[pd.DataFrame, list[dict]]:
    """Append one end-of-day bar; if NEPSE's adjusted previous close differs from
    our stored last close, rescale stored history first."""
    events = []
    d = pd.Timestamp(row["date"]).normalize()
    if len(hist):
        hist = hist[hist["date"] != d]
        last = hist.iloc[-1]
        pc = row.get("prev_close")
        if pc and pc == pc and last["close"] and abs(pc / last["close"] - 1) > 0.02 and pc < last["close"]:
            factor = pc / last["close"]
            if 0.2 < factor < 0.99:
                hist = _rescale_before(hist.reset_index(drop=True), len(hist), factor)
                events.append({"symbol": symbol, "date": d.strftime("%Y-%m-%d"), "factor": round(factor, 5),
                               "method": "daily_prev_close"})
    new = pd.DataFrame([{k: row.get(k) for k in ("date", "open", "high", "low", "close", "volume", "turnover")}])
    new["date"] = d
    out = pd.concat([hist, new], ignore_index=True) if len(hist) else new
    return out.sort_values("date").reset_index(drop=True), events
