"""Read portfolios from Google Sheets through the Apps Script bridge
(integrations/google_sheets/NepseScreenerBridge.gs).

Configure with the GitHub secret SHEET_PORTFOLIOS, one portfolio per line:

    Samvardhan Capital Management|https://script.google.com/macros/s/XXXX/exec|your-api-key
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .utils import log


def parse_sources(text: str | None) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[1].startswith("http"):
            out.append({"name": parts[0], "url": parts[1], "key": parts[2]})
        else:
            log.warning("Ignoring malformed SHEET_PORTFOLIOS line (expected Name|URL|KEY)")
    return out


def holdings_from_records(records: list[dict]) -> pd.DataFrame:
    """Combine sheet rows (possibly several accounts) into one row per symbol."""
    df = pd.DataFrame(records or [])
    if df.empty:
        return pd.DataFrame(columns=["symbol", "quantity", "wacc", "buy_date"])
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["wacc"] = pd.to_numeric(df.get("wacc"), errors="coerce")
    df["buy_date"] = pd.to_datetime(df.get("buy_date"), errors="coerce")
    df = df[df["quantity"] > 0]
    rows = []
    for sym, g in df.groupby("symbol"):
        has = g["wacc"].notna()
        wacc = float(np.average(g.loc[has, "wacc"], weights=g.loc[has, "quantity"])) if has.any() else np.nan
        rows.append({"symbol": sym, "quantity": float(g["quantity"].sum()), "wacc": wacc, "buy_date": g["buy_date"].min()})
    return pd.DataFrame(rows)


def fetch(source: dict, timeout: int = 60) -> tuple[pd.DataFrame, dict]:
    import requests

    r = requests.get(source["url"], params={"action": "holdings", "key": source["key"]}, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    if not js.get("ok"):
        raise RuntimeError(js.get("error") or "bridge returned an error")
    return holdings_from_records(js.get("holdings", [])), js


def fetch_all(text: str | None) -> list[tuple[str, pd.DataFrame]]:
    out = []
    for src in parse_sources(text):
        try:
            df, js = fetch(src)
            log.info("Sheet portfolio '%s': %d positions from tab '%s'", src["name"], len(df), js.get("sheet"))
            out.append((src["name"], df))
        except Exception as e:  # noqa: BLE001
            log.warning("Could not read sheet portfolio '%s': %s", src["name"], e)
    return out
