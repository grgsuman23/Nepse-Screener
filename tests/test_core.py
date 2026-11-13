"""Core unit tests:  python -m pytest -q"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener.adjust import append_day, auto_adjust  # noqa: E402
from screener.config import load_settings, load_strategies, materialize_strategy  # noqa: E402
from screener.costs import Costs  # noqa: E402
from screener.dsl import DSLError, Evaluator  # noqa: E402
from screener.indicators import compute, rsi  # noqa: E402
from screener.sources.parse import extract_fundamentals, parse_table  # noqa: E402

CFG = load_settings()


def test_commission_tiers_and_cgt():
    c = Costs(CFG)
    assert c.commission(1000) == 10  # minimum
    assert c.commission(450000) == pytest.approx(450000 * 0.0033)
    assert c.commission(3_000_000) == pytest.approx(3_000_000 * 0.0027)
    assert c.cgt_rate(100) == 0.05 and c.cgt_rate(400) == 0.0375
    b = c.sell_breakdown(1000, 560, 451552.5, 100)
    assert b["fees"] == pytest.approx(560000 * 0.0031 + 560000 * 0.00015 + 25)
    assert b["cgt"] == pytest.approx(b["gain_before_tax"] * 0.05)


def test_dsl_vectorised_and_safe():
    idx = pd.date_range("2024-01-01", periods=5)
    close = pd.DataFrame({"A": [1, 2, 3, 2, 5], "B": [5, 4, 3, 4, 6]}, index=idx, dtype=float)
    ma = pd.DataFrame({"A": [2] * 5, "B": [4] * 5}, index=idx, dtype=float)
    ev = Evaluator({"close": close, "ma": ma})
    m = ev.mask("cross_above(close, ma) and close > 1")
    assert m["A"].tolist() == [False, False, True, False, True]
    assert m["B"].tolist() == [False, False, False, False, True]
    bs = ev.eval("bars_since(close > 4)")
    assert bs["A"].iloc[-1] == 0 and bs["B"].iloc[1] == 1
    with pytest.raises(DSLError):
        ev.eval("__import__('os').system('ls')")
    with pytest.raises(DSLError):
        ev.eval("unknown_field > 1")


def test_auto_adjust_detects_bonus():
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=4), "open": [100, 102, 80, 81],
                       "high": [101, 103, 82, 82], "low": [99, 101, 79, 80], "close": [100, 102, 80, 81],
                       "volume": [10, 10, 10, 10]})
    out, ev = auto_adjust(df, "X")
    assert len(ev) == 1
    assert out["close"].iloc[1] == pytest.approx(80)  # history scaled to the post-bonus basis
    # daily append with NEPSE adjusted previous close
    hist = out.copy()
    new, ev2 = append_day(hist, {"date": "2024-01-05", "open": 75, "high": 76, "low": 74, "close": 75,
                                 "volume": 5, "turnover": 375, "prev_close": 73.64}, "X")
    assert len(ev2) == 1 and new["close"].iloc[-2] == pytest.approx(73.64, rel=1e-3)


def test_rsi_bounds_and_indicators():
    s = pd.Series(np.cumsum(np.random.default_rng(1).normal(0, 1, 300)) + 100)
    r = rsi(s, 14).dropna()
    assert r.between(0, 100).all()
    df = pd.DataFrame({"open": s, "high": s + 1, "low": s - 1, "close": s, "volume": 1000.0, "turnover": s * 1000})
    out = compute(df)
    for col in ("sma_200", "macd", "adx_14", "st_dir", "hh_55", "ret_3m"):
        assert col in out
    assert set(out["st_dir"].dropna().unique()) <= {-1.0, 0.0, 1.0}


def test_fundamental_label_parsing():
    html = """<table><tr><th>Sector</th><td>Commercial Banks</td></tr>
    <tr><th>EPS</th><td> 27.74 (FY:082-083, Q:1)</td></tr><tr><th>P/E Ratio</th><td>19.88</td></tr>
    <tr><th>Book Value</th><td>247.28</td></tr><tr><th>PBV</th><td>2.23</td></tr>
    <tr><th>% Dividend</th><td>12.50 (FY:081-082)</td></tr><tr><th>Shares Outstanding</th><td>27,05,69,984</td></tr>
    <tr><th>52 Weeks High - Low</th><td>568.00-471.00</td></tr></table>"""
    f = extract_fundamentals(html)
    assert f["sector"] == "Commercial Banks" and f["eps"] == pytest.approx(27.74)
    assert f["eps_period"] == "FY 082-083 Q1" and f["pe"] == pytest.approx(19.88)
    assert f["bvps"] == pytest.approx(247.28) and f["div_pct"] == pytest.approx(12.5)
    assert f["shares_outstanding"] == pytest.approx(270569984) and f["high_52w"] == 568
    rows = parse_table("<table><thead><tr><th>Symbol</th><th>LTP</th><th>% Change</th></tr></thead>"
                       "<tbody><tr><td>NABIL</td><td>551.5</td><td>0.29</td></tr></tbody></table>")
    assert rows[0]["symbol"] == "NABIL"


def test_strategy_materialize():
    s = {s["id"]: s for s in load_strategies()}
    m = materialize_strategy(s["momentum_rotation"])
    assert m["top_n"] == 8 and "{" not in m["rank_by"]
    t = materialize_strategy(s["trend_rider"], {"adx_min": 25, "stop": 2.5})
    assert "adx_14 > 25" in t["entry"] and t["stop_atr"] == 2.5


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from screener import engine
    from screener.config import get_paths
    from tools.make_demo_data import build_demo

    root = tmp_path_factory.mktemp("demo")
    build_demo(root, CFG)
    paths = get_paths(root)
    return engine.build(CFG, paths), paths


def test_backtest_accounting_reconciles(demo):
    from screener.backtest import Backtester

    a, _ = demo
    bt = Backtester(a, CFG)
    for s in load_strategies():
        r = bt.run(materialize_strategy(s))
        closed = sum(t.pnl for t in r.trades if t.exit_date)
        if not r.open_positions:
            assert r.equity.iloc[-1] == pytest.approx(CFG["backtest"]["initial_capital"] + closed, rel=1e-6)
        assert (r.equity > 0).all()
        assert all(t.qty >= 10 for t in r.trades)


def test_tracker_forward_returns(demo):
    from screener.tracker import forward_returns, simulated_calls, summarize

    a, _ = demo
    sim = simulated_calls(a, 300)
    out = forward_returns(a, sim, [5, 20])
    row = out.dropna(subset=["ret_5"]).iloc[0]
    d = a.dates.get_loc(pd.Timestamp(row["date"]))
    expect = a.w["close"][row["symbol"]].iloc[d + 5] / a.w["close"][row["symbol"]].iloc[d] - 1
    assert row["ret_5"] == pytest.approx(expect)
    s = summarize(out, "stance", [5, 20])
    assert {r["label"] for r in s} <= {"STRONG BUY", "BUY", "HOLD", "REDUCE", "EXIT"}


def test_portfolio_advice(demo):
    from screener.portfolio import analyze, load_portfolio

    a, paths = demo
    pf = analyze(a, load_portfolio(str(paths.root / "demo_portfolio.csv")), CFG)
    assert pf["positions"] and all(p["action"] in ("EXIT", "REDUCE", "HOLD", "ADD") for p in pf["positions"])
