"""Google Sheets bridge client (no network)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener import sheets  # noqa: E402


def test_parse_sources():
    s = sheets.parse_sources("# comment\nSamvardhan|https://script.google.com/macros/s/X/exec|k1\nbad line\n")
    assert s == [{"name": "Samvardhan", "url": "https://script.google.com/macros/s/X/exec", "key": "k1"}]


def test_holdings_combine_accounts():
    df = sheets.holdings_from_records([
        {"symbol": "nabil", "account": "A", "quantity": 100, "wacc": 500, "buy_date": "2025-01-01"},
        {"symbol": "NABIL", "account": "B", "quantity": 300, "wacc": 600, "buy_date": "2024-06-01"},
        {"symbol": "NTC", "account": "A", "quantity": 0, "wacc": 800, "buy_date": ""},
    ])
    r = df.set_index("symbol").loc["NABIL"]
    assert len(df) == 1 and r["quantity"] == 400 and r["wacc"] == pytest.approx(575)
    assert str(r["buy_date"].date()) == "2024-06-01"


def test_fetch_errors_and_success(monkeypatch):
    class Resp:
        def __init__(self, js): self.js = js
        def raise_for_status(self): pass
        def json(self): return self.js

    calls = {}

    def fake_get(url, params, timeout):
        calls["params"] = params
        return Resp({"ok": params["key"] == "good", "error": "Invalid or missing key", "sheet": "Portfolio",
                     "holdings": [{"symbol": "NABIL", "quantity": 10, "wacc": 500, "buy_date": "2025-01-01"}]})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    out = sheets.fetch_all("S|https://script.google.com/x|good\nT|https://script.google.com/y|bad")
    assert [n for n, _ in out] == ["S"] and calls["params"]["action"] == "holdings"
