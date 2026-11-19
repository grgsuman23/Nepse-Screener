"""Regime policy: recovery detection and the five bear-market rules."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.config import load_settings  # noqa: E402
from screener.market import recovery_mask, regime_name  # noqa: E402
from screener.scoring import stance_policy  # noqa: E402

CFG = load_settings()


def test_recovery_needs_turn_and_thrust():
    n = 120
    idx = pd.date_range("2025-01-01", periods=n)
    price = np.r_[np.linspace(1000, 800, 80), np.linspace(800, 900, 40)]
    m = pd.DataFrame({"index": price}, index=idx)
    m["ema_fast"] = m["index"].ewm(span=20, adjust=False).mean()
    m["breadth_50"] = np.r_[np.full(85, 0.15), np.full(35, 0.6)]   # jump 15% -> 60% = thrust
    m["new_highs"], m["new_lows"] = 0, 5
    rec, thrust = recovery_mask(m, CFG["regime"])
    assert thrust.iloc[85] and not thrust.iloc[:85].any()
    assert rec.iloc[-1] and not rec.iloc[:80].any()
    assert regime_name(0.5) == "recovery" and regime_name(-1) == "bear"


def frames(regime, score, sector_regime=1.0, rs=90.0, sec_rs=80.0):
    idx = pd.date_range("2026-01-01", periods=1)
    f = lambda v: pd.DataFrame({"A": [v]}, index=idx, dtype=float)  # noqa: E731
    w = {k: f(v) for k, v in dict(regime=regime, liquid=1, close=110, sma_200=100, ema_50=105, rsi_14=60,
                                   st_dir=1, dist_ema50=0.05, rs_rating=rs, sector_rs_rank=sec_rs,
                                   sector_regime=sector_regime, score_value=50, eps=10, roe_pct_sector=0.7,
                                   npl=np.nan).items()}
    return w, f(score)


def code(mode, **kw):
    w, s = frames(**kw)
    return int(stance_policy(w, s, CFG, mode)["code"].iloc[0, 0])


def test_modes_in_bear_market():
    # strong stock (score 80), NEPSE bear, its sector in an uptrend, a leader
    assert code("hard", regime=-1, score=80) == 0           # vetoed
    assert code("soft", regime=-1, score=80) == 1           # 80-8 = 72 >= 70 buy, < 83 strong
    assert code("leaders", regime=-1, score=80) == 2        # leader keeps full call
    assert code("leaders", regime=-1, score=80, rs=60) == 0  # non-leader vetoed
    assert code("sizing", regime=-1, score=80) == 2
    assert code("combined", regime=-1, score=80) == 1       # bear: sector leaders keep their score-based buy
    assert code("combined", regime=-1, score=80, sector_regime=-1, rs=60) == 0
    assert code("combined", regime=0.5, score=80) == 0      # recovery: no score-based buys (value dips only)
    assert code("combined", regime=1, score=80) == 2        # bull market: score-based buys remain


def test_sizing_and_accumulate():
    w, s = frames(regime=-1, score=50, sector_regime=-1, rs=40)
    w["score_value"].iloc[0, 0] = 85
    res = stance_policy(w, s, CFG, "combined")
    # value dip in a bear market -> STRONG BUY at half size
    assert res["accumulate"].iloc[0, 0] == 1 and res["code"].iloc[0, 0] == 2
    assert res["size"].iloc[0, 0] == 0.5 and res["stop"].iloc[0, 0] == 2.0
    w["regime"].iloc[0, 0] = 0.5                                      # recovery -> BUY
    assert stance_policy(w, s, CFG, "combined")["code"].iloc[0, 0] == 1
    w["regime"].iloc[0, 0] = 0.0                                      # neutral -> no buy
    assert stance_policy(w, s, CFG, "combined")["code"].iloc[0, 0] == 0
    cfg_old = {**CFG, "scoring": {**CFG["scoring"], "regime_policy": {**CFG["scoring"]["regime_policy"], "buy_rule": "accumulate"}}}
    w["regime"].iloc[0, 0] = -1.0
    res = stance_policy(w, s, cfg_old, "combined")
    assert res["accumulate"].iloc[0, 0] == 1 and res["code"].iloc[0, 0] == 0 and res["size"].iloc[0, 0] == 0.25
    w, s = frames(regime=1, score=80)
    res = stance_policy(w, s, CFG, "combined")
    assert res["size"].iloc[0, 0] == 1.0 and res["stop"].iloc[0, 0] == 3.0 and res["accumulate"].iloc[0, 0] == 0
