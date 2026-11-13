"""Offline smoke test of the data pipeline with a fake website (no network)."""
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener import store  # noqa: E402
from screener.config import get_paths, load_settings  # noqa: E402
from screener.pipeline import Pipeline  # noqa: E402


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def json(self):
        return json.loads(self.text)


def make_chart(n=300, start=100.0):
    days = pd.bdate_range(end=date.today() - timedelta(days=1), periods=n)
    t = [int(time.mktime(datetime(d.year, d.month, d.day).timetuple())) for d in days]
    c = [start * (1.001 ** i) for i in range(n)]
    return json.dumps({"s": "ok", "t": t, "o": c, "h": [x * 1.01 for x in c], "l": [x * 0.99 for x in c], "c": c,
                       "v": [1000 + i for i in range(n)]})


LATEST = """<table><thead><tr><th>Symbol</th><th>LTP</th><th>% Change</th><th>High</th><th>Low</th><th>Open</th>
<th>Qty.</th><th>Turnover</th></tr></thead><tbody>
<tr><td>AAA</td><td>150</td><td>1.5</td><td>151</td><td>148</td><td>149</td><td>1,000</td><td>1,50,000</td></tr>
<tr><td>BBB</td><td>250</td><td>-2</td><td>255</td><td>249</td><td>254</td><td>2,000</td><td>5,00,000</td></tr>
</tbody></table>"""
COMPANY = """<table><tr><th>Sector</th><td>Hydro Power</td></tr><tr><th>EPS</th><td>12.5 (FY:082-083, Q:1)</td></tr>
<tr><th>P/E Ratio</th><td>15</td></tr><tr><th>Book Value</th><td>120</td></tr><tr><th>% Dividend</th><td>5</td></tr></table>"""


class FakeHttp:
    def get(self, url, **kw):
        if "TechnicalChartHandler" in url:
            return FakeResp(make_chart())
        if "LatestMarket" in url:
            return FakeResp(LATEST)
        if "CompanyDetail" in url:
            return FakeResp(COMPANY)
        raise RuntimeError("blocked " + url)


def test_pipeline_offline(tmp_path):
    cfg = load_settings()
    paths = get_paths(tmp_path)
    pl = Pipeline(cfg, paths)
    pl.http = FakeHttp()
    pl._nepse_tried = True  # pretend the NEPSE API is down -> exercises fallbacks
    uni = pl.build_universe()
    assert set(uni["symbol"]) == {"AAA", "BBB"}
    pl.backfill()
    assert len(store.read_prices(paths, "AAA")) > 250
    assert len(store.read_index(paths)) > 250
    f = pl.update_fundamentals()
    assert f.set_index("symbol").loc["AAA", "eps"] == 12.5
    assert store.read_universe(paths).set_index("symbol").loc["AAA", "sector"] == "Hydro Power"
    pl.update_daily()
    st = json.loads(paths.status.read_text())
    assert any(v["ok"] for v in st.values())
