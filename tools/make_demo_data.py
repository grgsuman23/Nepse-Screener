"""Synthetic NEPSE-like market used to test the pipeline and preview the
dashboard. Tickers are clearly fake (e.g. DBNK01) — NOT real prices."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

SECTORS = {
    "Commercial Banks": ("DBNK", 9, 0.9, 1.4, (18, 36), (180, 260), (8, 15)),
    "Development Banks": ("DDEV", 5, 1.1, 1.9, (8, 22), (130, 170), (0, 10)),
    "Finance": ("DFIN", 4, 1.2, 2.2, (2, 18), (110, 150), (0, 8)),
    "Microfinance": ("DMFI", 7, 1.3, 2.5, (-5, 40), (150, 260), (0, 20)),
    "Life Insurance": ("DLIC", 5, 1.2, 2.0, (6, 20), (150, 260), (0, 10)),
    "Non Life Insurance": ("DNLI", 5, 1.1, 2.0, (10, 30), (170, 260), (5, 15)),
    "Hydro Power": ("DHPC", 9, 1.4, 2.8, (-4, 12), (90, 130), (0, 6)),
    "Manufacturing And Processing": ("DMFG", 4, 0.9, 2.2, (10, 60), (150, 400), (5, 30)),
    "Hotels And Tourism": ("DHTL", 3, 1.0, 2.3, (1, 10), (110, 160), (0, 5)),
    "Investment": ("DINV", 3, 1.2, 2.3, (2, 14), (110, 170), (0, 8)),
    "Trading": ("DTRD", 2, 0.8, 2.0, (20, 80), (200, 400), (10, 40)),
    "Others": ("DOTH", 3, 0.9, 1.8, (10, 40), (150, 300), (5, 20)),
}


def trading_days(start="2019-01-01", end="2026-11-12") -> pd.DatetimeIndex:
    d = pd.date_range(start, end, freq="D")
    d = d[d.weekday.isin([6, 0, 1, 2, 3])]
    rng = np.random.default_rng(7)
    holidays = rng.choice(len(d), size=int(len(d) * 0.04), replace=False)
    return d.delete(holidays)


def build_demo(root: Path, cfg: dict) -> None:
    root = Path(root)
    if (root / "data").exists():
        shutil.rmtree(root / "data")
    (root / "data" / "prices").mkdir(parents=True)
    (root / "data" / "index").mkdir(parents=True)
    (root / "data" / "fundamentals" / "snapshots").mkdir(parents=True)
    rng = np.random.default_rng(42)
    dates = trading_days()
    n = len(dates)
    # --- regime-switching market factor (NEPSE-like long bull / bear waves)
    mkt = np.zeros(n)
    i = 0
    state = 0
    while i < n:
        dur = int(rng.integers(70, 280))
        mu, sd = [(0.0012, 0.010), (-0.0011, 0.012), (0.0001, 0.008)][state]
        mkt[i:i + dur] = rng.normal(mu, sd, size=min(dur, n - i))
        i += dur
        state = int(rng.choice([0, 1, 2], p=[0.45, 0.30, 0.25] if state != 0 else [0.15, 0.45, 0.40]))
    mkt[-170:] = rng.normal(0.0013, 0.009, 170)  # end the demo in a bull phase
    idx = 1300 * np.cumprod(1 + mkt)
    pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "open": idx, "high": idx * 1.004, "low": idx * 0.996,
                  "close": np.round(idx, 2), "volume": 0}).to_csv(root / "data" / "index" / "NEPSE.csv", index=False)
    uni, fund = [], []
    for sector, (pfx, count, beta_m, vol_m, eps_r, bv_r, div_r) in SECTORS.items():
        sec_f = np.zeros(n)
        for t in range(1, n):  # persistent sector rotation factor
            sec_f[t] = 0.97 * sec_f[t - 1] + rng.normal(0, 0.0016)
        for k in range(1, count + 1):
            sym = f"{pfx}{k:02d}"
            beta = beta_m * rng.uniform(0.7, 1.3)
            ivol = 0.006 * vol_m * rng.uniform(0.7, 1.3)
            alpha = rng.normal(0.0, 0.0003)
            mom = np.zeros(n)
            for t in range(1, n):
                mom[t] = 0.98 * mom[t - 1] + rng.normal(0, 0.0004)
            r = beta * mkt + sec_f * 0.5 + mom + alpha + rng.normal(0, ivol, n)
            r = np.clip(r, -0.099, 0.099)
            listed = int(rng.integers(0, 260)) if rng.random() < 0.25 else 0
            close = np.cumprod(1 + r)
            close = close / close[-1] * rng.uniform(180, 1100)  # realistic current price
            gap = rng.normal(0, ivol / 3, n)
            open_ = np.r_[close[0], close[:-1]] * (1 + gap)
            hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, ivol / 2, n)))
            lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, ivol / 2, n)))
            liq = rng.choice([0.3, 1, 3, 8], p=[0.2, 0.35, 0.3, 0.15])
            base_vol = liq * 4e6 / close.mean()
            vol = base_vol * np.exp(rng.normal(0, 0.5, n)) * (1 + 25 * np.abs(r))
            vol[rng.random(n) < 0.02] *= 4  # accumulation spikes
            if liq < 0.5:
                vol[rng.random(n) < 0.15] = 0  # illiquid: some no-trade days
            df = pd.DataFrame({"date": dates, "open": open_, "high": hi, "low": lo, "close": close,
                               "volume": np.round(vol), "turnover": np.round(vol * close)})
            df = df.iloc[listed:]
            df = df[df["volume"] > 0]
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
            df.round(2).to_csv(root / "data" / "prices" / f"{sym}.csv", index=False)
            uni.append({"symbol": sym, "name": f"Demo {sector.split()[0]} {k} Ltd", "sector": sector, "security_id": None})
            pe_t = rng.uniform(10, 45)
            eps = close[-1] / pe_t if rng.random() > 0.08 else -rng.uniform(1, 8)
            bv = close[-1] / rng.uniform(0.9, 4.0)
            fund.append({"symbol": sym, "sector": sector, "eps": round(eps, 2), "eps_period": "FY 082-083 Q1",
                         "pe": round(close[-1] / eps, 2) if eps > 0 else None, "bvps": round(bv, 2),
                         "pbv": round(close[-1] / bv, 2), "div_pct": round(rng.uniform(*div_r), 2),
                         "bonus_pct": round(rng.uniform(0, 10), 2) if rng.random() < 0.4 else 0,
                         "shares_outstanding": int(rng.uniform(5e6, 3e8)), "source": "demo", "as_of": "2026-11-12"})
    pd.DataFrame(uni).to_csv(root / "data" / "universe.csv", index=False)
    fd = pd.DataFrame(fund)
    fd.to_csv(root / "data" / "fundamentals" / "latest.csv", index=False)
    fd.to_csv(root / "data" / "fundamentals" / "snapshots" / "2026-11-12.csv", index=False)
    # demo portfolio
    picks = rng.choice([u["symbol"] for u in uni], size=7, replace=False)
    rows = []
    for s in picks:
        px = pd.read_csv(root / "data" / "prices" / f"{s}.csv")
        j = int(rng.integers(len(px) - 420, len(px) - 30))
        rows.append({"symbol": s, "quantity": int(rng.integers(5, 60)) * 10,
                     "wacc": round(px["close"].iloc[j] * rng.uniform(0.97, 1.03), 2), "buy_date": px["date"].iloc[j]})
    pd.DataFrame(rows).to_csv(root / "demo_portfolio.csv", index=False)
