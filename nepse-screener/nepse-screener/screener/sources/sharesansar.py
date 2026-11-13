"""ShareSansar.com fundamentals adapter (fallback for EPS / P/E / Book value)."""
from __future__ import annotations

from ..utils import Http
from .parse import extract_fundamentals

BASE = "https://www.sharesansar.com"


class ShareSansar:
    name = "sharesansar"

    def __init__(self, http: Http):
        self.http = http

    def fundamentals(self, symbol: str) -> dict:
        r = self.http.get(f"{BASE}/company/{symbol.lower()}")
        data = extract_fundamentals(r.text)
        data["source"] = self.name
        return data
