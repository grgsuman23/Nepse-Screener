"""Date calibration, one-time repair and the intraday (live) update."""
import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener import store  # noqa: E402
from screener.config import get_paths, load_settings  # noqa: E402
from screener.pipeline import Pipeline, best_day_offset, is_trading_weekday  # noqa: E402

CFG = load_settings()


def bars(dates, start=100.0):
    n = len(dates)
    c = [start + i * 1.7 for i in range(n)]
    return pd.DataFrame({"date": pd.to_datetime(dates), "open": [x - 1 for x in c], "high": [x + 2 for x in c],
                         "low": [x - 2 for x in c], "close": c, "volume": 1000.0, "turnover": 1e5})


def test_best_offset_detects_one_day_late_feed():
    truth = bars(pd.bdate_range("2026-09-01", periods=30))
    late = truth.copy()
    late["date"] = late["date"] + pd.Timedelta(days=1)
    assert best_day_offset(truth, late) == -1
    assert best_day_offset(truth, truth) == 0
    assert is_trading_weekday(pd.Timestamp("2026-11-13").date())      # Friday trades now
    assert not is_trading_weekday(pd.Timestamp("2026-11-14").date())  # Saturday never


def test_repair_moves_stored_dates_once(tmp_path):
    paths = get_paths(tmp_path)
    store.write_prices(paths, "AAA", bars(["2026-11-14", "2026-11-18", "2026-11-19"]))
    store.write_index(paths, bars(["2026-11-14", "2026-11-19"])[["date", "close"]])
    (paths.tracking / "recommendations.csv").write_text("date,symbol,stance\n2026-11-18,AAA,BUY\n")
    pl = Pipeline(CFG, paths)
    pl._nepse_tried = True  # no NEPSE access -> default offset -1
    pl.repair_dates()
    assert store.read_prices(paths, "AAA")["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-11-13", "2026-11-17", "2026-11-18"]
    assert pd.read_csv(paths.tracking / "recommendations.csv")["date"].tolist() == ["2026-11-17"]
    pl2 = Pipeline(CFG, paths)
    pl2._nepse_tried = True
    pl2.repair_dates()  # second run: nothing moves again
    assert store.read_prices(paths, "AAA")["date"].iloc[-1].strftime("%Y-%m-%d") == "2026-11-18"


def test_intraday_snapshot(tmp_path, monkeypatch):
    import run
    from screener.sources import nepse_api
    from tools.make_demo_data import build_demo

    build_demo(tmp_path, CFG)
    paths = get_paths(tmp_path)
    syms = store.stored_symbols(paths)
    last = {s: store.read_prices(paths, s).iloc[-1] for s in syms}

    class Fake:
        def market_open(self): return True
        def live(self):
            return pd.DataFrame([{"symbol": s, "open": r["close"], "high": r["close"] * 1.03, "low": r["close"] * 0.99,
                                  "close": r["close"] * 1.02, "volume": 5000.0, "turnover": 5000 * r["close"],
                                  "prev_close": r["close"]} for s, r in last.items()])
        def index_live(self): return {"close": 1500.0, "high": 1510.0, "low": 1490.0, "prev_close": 1495.0}

    monkeypatch.setattr(nepse_api, "try_create", lambda: Fake())
    hist_before = sorted(p.name for p in (paths.out / "history").glob("*.json"))
    run.cmd_intraday(CFG, paths, types.SimpleNamespace(force=False))
    meta = json.loads((paths.out / "meta.json").read_text())
    scr = json.loads((paths.out / "screener.json").read_text())
    assert meta["live"]["market_open"] and meta["live"]["stocks"] == len(syms)
    assert scr["date"] == meta["live"]["date"] == meta["last_trade_date"]
    row = scr["rows"][0]
    assert row["chg_pct"] == pytest.approx(0.02, abs=1e-6)
    # stored history is untouched by intraday runs
    assert store.read_prices(paths, syms[0]).iloc[-1]["close"] == pytest.approx(last[syms[0]]["close"])
    assert sorted(p.name for p in (paths.out / "history").glob("*.json")) == hist_before
