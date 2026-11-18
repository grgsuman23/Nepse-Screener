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
    assert [n for n, _, _ in out] == ["S"] and calls["params"]["action"] == "holdings"


def test_flat_cgt_and_advice_push(monkeypatch):
    import json

    import numpy as np
    import requests

    from screener import sheets
    from screener.costs import Costs

    class Resp:
        def __init__(self, js): self.js = js
        def raise_for_status(self): pass
        def json(self): return self.js

    monkeypatch.setattr(requests, "get", lambda url, params, timeout: Resp(
        {"ok": True, "sheet": "Holdings", "tax": {"cgt_flat": 0.1}, "accepts_advice": True,
         "holdings": [{"symbol": "NABIL", "quantity": 10, "wacc": 500}]}))
    (name, df, cgt, src), = sheets.fetch_all("S|https://script.google.com/x|k", with_source=True)
    assert cgt == 0.1 and src["accepts_advice"]
    assert Costs({}, cgt).cgt_rate(10) == 0.1 and Costs({}, cgt).cgt_rate(1000) == 0.1

    sent = {}

    def fake_post(url, data, headers, timeout):
        sent.update(json.loads(data))
        return Resp({"ok": True, "rows": 1})

    monkeypatch.setattr(requests, "post", fake_post)
    assert sheets.push_advice(src, {"summary": {"value": np.float64("nan")}, "positions": [{"qty": np.int64(5)}]})
    assert sent["key"] == "k" and sent["advice"]["summary"]["value"] is None and sent["advice"]["positions"][0]["qty"] == 5
