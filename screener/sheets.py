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


def fetch_all(text: str | None, with_source: bool = False) -> list[tuple]:
    """Returns (name, holdings, flat CGT rate or None[, source]) per configured sheet."""
    out = []
    for src in parse_sources(text):
        try:
            df, js = fetch(src)
            log.info("Sheet portfolio '%s': %d positions from tab '%s'", src["name"], len(df), js.get("sheet"))
            cgt = (js.get("tax") or {}).get("cgt_flat")
            src["accepts_advice"] = bool(js.get("accepts_advice"))
            row = (src["name"], df, float(cgt) if cgt not in (None, "") else None)
            out.append(row + (src,) if with_source else row)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not read sheet portfolio '%s': %s", src["name"], e)
    return out


def _clean(o):
    """JSON-safe copy (NaN -> None, numpy -> python)."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    return o


def push_advice(source: dict, result: dict, timeout: int = 90) -> bool:
    """Send the full position-aware advice back to the bridge, which writes it
    into the sheet's advice tab. Apps Script runs doPost on the POST and then
    redirects to the output, which requests follows with a GET."""
    import json

    import requests

    body = {"key": source["key"], "action": "advice", "advice": _clean(result)}
    try:
        r = requests.post(source["url"], data=json.dumps(body), headers={"Content-Type": "text/plain"}, timeout=timeout)
        js = r.json()
        if js.get("ok"):
            log.info("Advice written to sheet '%s' (%s rows)", source["name"], js.get("rows"))
            return True
        log.warning("Sheet '%s' did not accept the advice: %s", source["name"], js.get("error"))
    except Exception as e:  # noqa: BLE001
        log.warning("Could not send advice to sheet '%s': %s", source["name"], e)
    return False
