"""Merolagani.com adapters.

* history()      – TradingView-style chart feed with bonus/right ADJUSTED prices
* latest()       – today's price table (LatestMarket.aspx)
* fundamentals() – company page key-figures table (EPS, P/E, Book value...)
"""
from __future__ import annotations

import time
from datetime import date, datetime

import pandas as pd

from ..utils import Http, log, to_num
from .parse import extract_fundamentals, parse_table

BASE = "https://merolagani.com"
CHART = (
    BASE + "/handlers/TechnicalChartHandler.ashx?type=get_advanced_chart&symbol={symbol}"
    "&resolution=1D&rangeStartDate={start}&rangeEndDate={end}&from=&isAdjust={adj}&currencyCode=NPR"
)


class Merolagani:
    name = "merolagani"

    def __init__(self, http: Http):
        self.http = http

    def history(self, symbol: str, start: date, end: date | None = None, adjusted: bool = True) -> pd.DataFrame:
        end = end or date.today()
        s = int(time.mktime(datetime(start.year, start.month, start.day).timetuple()))
        e = int(time.mktime(datetime(end.year, end.month, end.day, 23, 59).timetuple()))
        url = CHART.format(symbol=symbol, start=s, end=e, adj=1 if adjusted else 0)
        r = self.http.get(url, headers={"Referer": f"{BASE}/CompanyDetail.aspx?symbol={symbol}",
                                        "X-Requested-With": "XMLHttpRequest"})
        js = r.json()
        if not isinstance(js, dict) or js.get("s") not in ("ok", None) or not js.get("t"):
            return pd.DataFrame()
        df = pd.DataFrame(
            {
                # timestamps may be UTC- or NPT-midnight: view them in Nepal time
                "date": pd.to_datetime(js["t"], unit="s", utc=True)
                .tz_convert("Asia/Kathmandu").tz_localize(None).normalize(),
                "open": js.get("o"),
                "high": js.get("h"),
                "low": js.get("l"),
                "close": js.get("c"),
                "volume": js.get("v"),
            }
        )
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["turnover"] = df["close"] * df["volume"]  # approximation (feed has no turnover)
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        return df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)

    def latest(self) -> pd.DataFrame:
        r = self.http.get(f"{BASE}/LatestMarket.aspx")
        rows = parse_table(r.text, "symbol")
        out = []
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue

            def g(*names):
                for n in names:
                    for k, v in row.items():
                        if n in k:
                            return to_num(v)
                return float("nan")

            ltp = g("ltp", "close")
            chg = g("% change", "%change", "change")
            out.append(
                {
                    "symbol": sym,
                    "close": ltp,
                    "open": g("open"),
                    "high": g("high"),
                    "low": g("low"),
                    "volume": g("qty", "volume"),
                    "turnover": g("turnover", "amount"),
                    "prev_close": ltp / (1 + chg / 100) if chg == chg else float("nan"),
                }
            )
        df = pd.DataFrame(out)
        if df.empty:
            return df
        for c in ("open", "high", "low"):
            df[c] = df[c].fillna(df["close"])
        return df

    def fundamentals(self, symbol: str) -> dict:
        r = self.http.get(f"{BASE}/CompanyDetail.aspx?symbol={symbol}")
        data = extract_fundamentals(r.text)
        data["source"] = self.name
        return data


def try_create(http: Http) -> Merolagani:
    return Merolagani(http)
