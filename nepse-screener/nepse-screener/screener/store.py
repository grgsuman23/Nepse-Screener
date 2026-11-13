"""CSV storage — one file per stock so daily updates are tiny git diffs."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .config import Paths

PRICE_COLS = ["date", "open", "high", "low", "close", "volume", "turnover"]
FUND_COLS = [
    "symbol", "sector", "eps", "eps_period", "pe", "bvps", "pbv", "div_pct", "bonus_pct",
    "shares_outstanding", "mcap", "roe", "npl", "car", "high_52w", "low_52w", "source", "as_of",
]


def _sym_file(paths: Paths, symbol: str) -> Path:
    return paths.prices / f"{symbol.upper().replace('/', '_')}.csv"


def read_prices(paths: Paths, symbol: str) -> pd.DataFrame:
    f = _sym_file(paths, symbol)
    if not f.exists():
        return pd.DataFrame(columns=PRICE_COLS)
    df = pd.read_csv(f, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def write_prices(paths: Paths, symbol: str, df: pd.DataFrame) -> None:
    out = df.copy()
    for c in PRICE_COLS:
        if c not in out:
            out[c] = float("nan")
    out = out[PRICE_COLS].dropna(subset=["close"]).drop_duplicates("date", keep="last").sort_values("date")
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].round(2)
    out["volume"] = out["volume"].round(0)
    out["turnover"] = out["turnover"].round(0)
    out.to_csv(_sym_file(paths, symbol), index=False)


def stored_symbols(paths: Paths) -> list[str]:
    return sorted(p.stem for p in paths.prices.glob("*.csv"))


def load_all_prices(paths: Paths, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    symbols = symbols or stored_symbols(paths)
    out = {}
    for s in symbols:
        df = read_prices(paths, s)
        if len(df):
            out[s] = df
    return out


def read_universe(paths: Paths) -> pd.DataFrame:
    if paths.universe.exists():
        return pd.read_csv(paths.universe, dtype={"symbol": str})
    return pd.DataFrame(columns=["symbol", "name", "sector", "security_id"])


def write_universe(paths: Paths, df: pd.DataFrame) -> None:
    df.to_csv(paths.universe, index=False)


def read_index(paths: Paths, name: str = "NEPSE") -> pd.DataFrame:
    f = paths.index / f"{name}.csv"
    if not f.exists():
        return pd.DataFrame(columns=["date", "close"])
    return pd.read_csv(f, parse_dates=["date"]).sort_values("date").reset_index(drop=True)


def write_index(paths: Paths, df: pd.DataFrame, name: str = "NEPSE") -> None:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.drop_duplicates("date", keep="last").to_csv(paths.index / f"{name}.csv", index=False)


def read_fundamentals(paths: Paths) -> pd.DataFrame:
    f = paths.fundamentals / "latest.csv"
    if not f.exists():
        return pd.DataFrame(columns=FUND_COLS)
    return pd.read_csv(f, dtype={"symbol": str})


def write_fundamentals(paths: Paths, df: pd.DataFrame, snapshot_date: date | None = None) -> None:
    paths.fundamentals.mkdir(parents=True, exist_ok=True)
    df.to_csv(paths.fundamentals / "latest.csv", index=False)
    d = (snapshot_date or date.today()).isoformat()
    df.to_csv(paths.snapshots / f"{d}.csv", index=False)


def load_snapshots(paths: Paths) -> pd.DataFrame:
    """All fundamental snapshots as one long frame with a `snap_date` column."""
    frames = []
    for f in sorted(paths.snapshots.glob("*.csv")):
        try:
            df = pd.read_csv(f, dtype={"symbol": str})
            df["snap_date"] = pd.to_datetime(f.stem)
            frames.append(df)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def read_manual_fundamentals(config_dir: Path) -> pd.DataFrame:
    f = config_dir / "fundamentals_manual.csv"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f, dtype={"symbol": str}, comment="#")
    df["symbol"] = df["symbol"].str.upper().str.strip()
    return df


def append_corporate_actions(paths: Paths, events: list[dict]) -> None:
    if not events:
        return
    new = pd.DataFrame(events)
    f = paths.corporate_actions
    if f.exists():
        new = pd.concat([pd.read_csv(f), new], ignore_index=True)
    new.drop_duplicates(["symbol", "date"], keep="last").to_csv(f, index=False)
