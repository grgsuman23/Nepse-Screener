"""Official nepalstock.com data through the open-source `nepse` library
(github.com/basic-bgnr/NepseUnofficialApi), which handles NEPSE's token
scrambling. Prices from NEPSE are NOT adjusted for bonus/right shares —
see screener/adjust.py."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ..utils import log, to_num

_FIELD_ALIASES = {
    "date": ["businessDate", "business_date", "date", "tradeDate"],
    "open": ["openPrice", "open", "openingPrice"],
    "high": ["highPrice", "high", "maxPrice"],
    "low": ["lowPrice", "low", "minPrice"],
    "close": ["closePrice", "close", "lastTradedPrice", "ltp", "closingPrice"],
    "volume": ["totalTradedQuantity", "totalTradeQuantity", "volume", "tradedQuantity"],
    "turnover": ["totalTradedValue", "totalTradeValue", "turnover", "amount"],
    "prev_close": ["previousDayClosePrice", "previousClose", "prevClose"],
    "symbol": ["symbol", "securitySymbol", "stockSymbol"],
}


def _pick(rec: dict, key: str):
    for k in _FIELD_ALIASES[key]:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
    return None


def normalize_records(records: list[dict], with_symbol: bool = False) -> pd.DataFrame:
    rows = []
    for r in records or []:
        d = _pick(r, "date")
        if d is None:
            continue
        row = {
            "date": pd.to_datetime(str(d)[:10], errors="coerce"),
            "open": to_num(_pick(r, "open")),
            "high": to_num(_pick(r, "high")),
            "low": to_num(_pick(r, "low")),
            "close": to_num(_pick(r, "close")),
            "volume": to_num(_pick(r, "volume")),
            "turnover": to_num(_pick(r, "turnover")),
            "prev_close": to_num(_pick(r, "prev_close")),
        }
        if with_symbol:
            row["symbol"] = str(_pick(r, "symbol") or "").upper()
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]
    return df.sort_values("date").reset_index(drop=True)


class NepseApi:
    name = "nepse_api"

    def __init__(self):
        from nepse import Nepse  # imported lazily so the rest works without it

        self.n = Nepse()
        try:
            self.n.setTLSVerification(False)  # nepalstock.com's certificate chain is often broken
        except Exception:
            pass
        self._ids: dict[str, int] | None = None

    # ---------------- universe ----------------
    def universe(self) -> pd.DataFrame:
        companies = self.n.getCompanyList()
        rows = []
        for c in companies:
            rows.append(
                {
                    "symbol": str(c.get("symbol", "")).upper(),
                    "name": c.get("securityName") or c.get("companyName") or "",
                    "sector": c.get("sectorName") or "",
                    "instrument": c.get("instrumentType") or "",
                    "status": c.get("status") or "",
                    "security_id": c.get("id"),
                }
            )
        df = pd.DataFrame(rows)
        if "status" in df:
            df = df[(df["status"].astype(str).str.upper().isin(["A", "ACTIVE", ""]))]
        if "instrument" in df:
            ok = df["instrument"].astype(str).str.lower().str.contains("equ") | (df["instrument"] == "")
            df = df[ok]
        return df.drop_duplicates("symbol").reset_index(drop=True)

    # ---------------- history ----------------
    def history(self, symbol: str, start: date, end: date | None = None) -> pd.DataFrame:
        end = end or date.today()
        frames = []
        # The endpoint returns at most ~500 rows; walk the range in 1-year chunks.
        cur = start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=365))
            data = self.n.getCompanyPriceVolumeHistory(symbol, cur, chunk_end)
            recs = data.get("content", data) if isinstance(data, dict) else data
            df = normalize_records(recs if isinstance(recs, list) else [])
            if not df.empty:
                frames.append(df)
            cur = chunk_end + timedelta(days=1)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)

    # ---------------- end of day (all stocks, one call) ----------------
    def day(self, business_date: date | None = None) -> pd.DataFrame:
        bd = business_date or date.today()
        data = self.n.getPriceVolumeHistory(business_date=bd)
        recs = data.get("content", data) if isinstance(data, dict) else data
        df = normalize_records(recs if isinstance(recs, list) else [], with_symbol=True)
        return df

    # ---------------- live (during market hours) ----------------
    def live(self) -> pd.DataFrame:
        """Live market: one row per traded stock with the latest price.
        Falls back to today's price-volume table when the live feed is empty."""
        data = []
        for fn in ("getLiveMarket", "getPriceVolume"):
            try:
                data = getattr(self.n, fn)() or []
            except Exception as e:  # noqa: BLE001
                log.warning("NEPSE %s failed: %r", fn, e)
                data = []
            if isinstance(data, dict):
                data = data.get("content", [])
            if data:
                break
        rows = []
        for r in data:
            sym = str(_pick(r, "symbol") or "").upper()
            close = to_num(_pick(r, "close"))
            if not sym or not close or close != close:
                continue
            rows.append({"symbol": sym, "open": to_num(_pick(r, "open")), "high": to_num(_pick(r, "high")),
                         "low": to_num(_pick(r, "low")), "close": close, "volume": to_num(_pick(r, "volume")),
                         "turnover": to_num(_pick(r, "turnover")), "prev_close": to_num(_pick(r, "prev_close")),
                         "updated": r.get("lastUpdatedDateTime") or r.get("lastUpdatedTime")})
        return pd.DataFrame(rows)

    def index_live(self) -> dict | None:
        """Current NEPSE index value (live during the session)."""
        data = self.n.getNepseIndex() or []
        for r in data if isinstance(data, list) else []:
            name = str(r.get("index") or r.get("indexName") or "").strip().lower()
            if name in ("nepse index", "nepse"):
                cur = to_num(r.get("currentValue") or r.get("close"))
                return {"close": cur, "high": to_num(r.get("high")), "low": to_num(r.get("low")),
                        "prev_close": to_num(r.get("previousClose")), "time": r.get("generatedTime")}
        return None

    def market_open(self) -> bool | None:
        try:
            st = self.n.getMarketStatus() or {}
            return "OPEN" in str(st.get("isOpen", "")).upper()
        except Exception:  # noqa: BLE001
            return None

    def index_history(self) -> pd.DataFrame:
        """NEPSE index daily graph -> DataFrame[date, close]."""
        data = self.n.getDailyNepseIndexGraph()
        rows = []
        for item in data or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                ts, val = item[0], item[1]
                d = pd.to_datetime(ts, unit="s" if ts > 1e9 and ts < 1e11 else "ms", errors="coerce")
                rows.append({"date": d.normalize(), "close": to_num(val)})
            elif isinstance(item, dict):
                rows.append({"date": pd.to_datetime(str(item.get("businessDate") or item.get("date"))[:10]),
                             "close": to_num(item.get("closingIndex") or item.get("close") or item.get("value"))})
        df = pd.DataFrame(rows).dropna()
        if df.empty:
            return df
        return df.groupby("date", as_index=False)["close"].last()


def try_create() -> NepseApi | None:
    try:
        return NepseApi()
    except Exception as e:
        log.warning("NEPSE API unavailable: %s", e)
        return None
