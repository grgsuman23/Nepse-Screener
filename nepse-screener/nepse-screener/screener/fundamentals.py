"""Fundamental ratios, point-in-time joins and sector-relative valuation."""
from __future__ import annotations

import numpy as np
import pandas as pd

BASE_FIELDS = ["eps", "bvps", "div_pct", "bonus_pct", "shares_outstanding", "roe_reported", "npl", "car"]


def choose_mode(snapshots: pd.DataFrame, last_date: pd.Timestamp, mode: str) -> str:
    if mode != "auto":
        return mode
    if snapshots is None or snapshots.empty:
        return "current_backfill"
    first = snapshots["snap_date"].min()
    return "point_in_time" if (last_date - first).days >= 365 else "current_backfill"


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "roe" in df:
        df["roe_reported"] = pd.to_numeric(df["roe"], errors="coerce")
        # sites publish ROE in % ; store as fraction
        df.loc[df["roe_reported"].abs() > 1.5, "roe_reported"] = df["roe_reported"] / 100
    for c in BASE_FIELDS:
        if c not in df:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def wide_base_fields(
    symbols: list[str], dates: pd.DatetimeIndex, latest: pd.DataFrame, snapshots: pd.DataFrame, mode: str
) -> dict[str, pd.DataFrame]:
    """Return {field: wide DataFrame(dates x symbols)} of raw fundamentals."""
    out = {f: pd.DataFrame(np.nan, index=dates, columns=symbols) for f in BASE_FIELDS}
    if mode == "point_in_time" and snapshots is not None and len(snapshots):
        snaps = _prep(snapshots)
        for f in BASE_FIELDS:
            piv = snaps.pivot_table(index="snap_date", columns="symbol", values=f, aggfunc="last")
            piv = piv.reindex(columns=symbols)
            out[f] = piv.reindex(piv.index.union(dates)).sort_index().ffill().reindex(dates)
    elif latest is not None and len(latest):
        lat = _prep(latest).drop_duplicates("symbol").set_index("symbol")
        for f in BASE_FIELDS:
            vals = lat[f].reindex(symbols)
            out[f] = pd.DataFrame(np.tile(vals.to_numpy(), (len(dates), 1)), index=dates, columns=symbols)
    return out


def derive(close: pd.DataFrame, base: dict[str, pd.DataFrame], face_value: float = 100) -> dict[str, pd.DataFrame]:
    eps, bvps = base["eps"], base["bvps"]
    f = {}
    f["eps"], f["bvps"] = eps, bvps
    f["pe"] = (close / eps).where(eps > 0)
    f["pbv"] = (close / bvps).where(bvps > 0)
    roe = (eps / bvps).where(bvps > 0)
    f["roe"] = base["roe_reported"].fillna(roe) if "roe_reported" in base else roe
    f["earnings_yield"] = eps / close
    f["div_yield"] = (base["div_pct"] / 100 * face_value) / close
    f["bonus_pct"] = base["bonus_pct"]
    g = np.sqrt((22.5 * eps * bvps).where((eps > 0) & (bvps > 0)))
    f["graham"] = g
    f["graham_discount"] = g / close - 1
    f["mcap"] = close * base["shares_outstanding"]
    f["npl"], f["car"] = base["npl"], base["car"]
    return f


def sector_relative(fields: dict[str, pd.DataFrame], sectors: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Sector medians and within-sector percentiles (per date)."""
    cols = fields["pe"].columns
    out = {k: pd.DataFrame(np.nan, index=fields["pe"].index, columns=cols) for k in
           ("pe_vs_sector", "pbv_vs_sector", "sector_pe_median", "sector_pbv_median",
            "pe_pct_sector", "pbv_pct_sector", "dy_pct_sector", "graham_pct_sector", "roe_pct_sector")}
    by_sector: dict[str, list[str]] = {}
    for s in cols:
        by_sector.setdefault(sectors.get(s) or "Others", []).append(s)
    for sec, syms in by_sector.items():
        pe, pbv = fields["pe"][syms], fields["pbv"][syms]
        pe_med = pe.median(axis=1)
        pbv_med = pbv.median(axis=1)
        out["sector_pe_median"][syms] = np.repeat(pe_med.to_numpy()[:, None], len(syms), axis=1)
        out["sector_pbv_median"][syms] = np.repeat(pbv_med.to_numpy()[:, None], len(syms), axis=1)
        out["pe_vs_sector"][syms] = pe.div(pe_med, axis=0)
        out["pbv_vs_sector"][syms] = pbv.div(pbv_med, axis=0)
        # lower P/E & P/B = better -> rank descending
        out["pe_pct_sector"][syms] = (-pe).rank(axis=1, pct=True)
        out["pbv_pct_sector"][syms] = (-pbv).rank(axis=1, pct=True)
        out["dy_pct_sector"][syms] = fields["div_yield"][syms].rank(axis=1, pct=True)
        out["graham_pct_sector"][syms] = fields["graham_discount"][syms].rank(axis=1, pct=True)
        roe = fields["roe"][syms].where(fields["eps"][syms] > 0)
        out["roe_pct_sector"][syms] = roe.rank(axis=1, pct=True)
    # loss-makers get the worst value percentile instead of being ignored
    loss = fields["eps"] <= 0
    for k in ("pe_pct_sector", "roe_pct_sector"):
        out[k] = out[k].mask(loss, 0.0)
    return out
