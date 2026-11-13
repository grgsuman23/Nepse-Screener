"""Parameter optimisation with an out-of-sample check.

Searching many parameter sets on the same data finds settings that merely
fit the past. We therefore pick the best set on the TRAINING period only, then
report how that set did on the unseen TEST period. A strategy whose test
performance collapses is flagged as over-fitted."""
from __future__ import annotations

import itertools
import random

import pandas as pd

from .backtest import Backtester
from .config import materialize_strategy
from .utils import log


def _score(m: dict, objective: str) -> float:
    if not m:
        return -1e9
    if objective == "sharpe":
        return m.get("sharpe", -1e9)
    if objective == "cagr":
        return m.get("cagr", -1e9)
    cagr, mdd = m.get("cagr", 0), m.get("max_drawdown", 0)
    return cagr / abs(mdd) if mdd < 0 else cagr * 10


def optimize(bt: Backtester, strategy: dict, cfg: dict, start: str | None = None, end: str | None = None) -> dict:
    grid = strategy.get("params") or {}
    if not grid:
        return {}
    oc = cfg.get("optimize", {})
    objective = oc.get("objective", "calmar")
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    maxc = int(oc.get("max_combinations", 60))
    if len(combos) > maxc:
        random.Random(42).shuffle(combos)
        combos = combos[:maxc]
    dates = bt.a.dates
    if start:
        dates = dates[dates >= pd.Timestamp(start)]
    if end:
        dates = dates[dates <= pd.Timestamp(end)]
    split = dates[int(len(dates) * float(oc.get("train_fraction", 0.7)))]
    train_end = (split - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    test_start = split.strftime("%Y-%m-%d")
    rows = []
    for combo in combos:
        params = dict(zip(keys, combo))
        s = materialize_strategy(strategy, params)
        try:
            tr = bt.run(s, start=str(dates[0].date()), end=train_end)
            te = bt.run(s, start=test_start, end=str(dates[-1].date()))
        except Exception as e:  # noqa: BLE001
            log.debug("combo %s failed: %s", params, e)
            continue
        rows.append({"params": params, "train": _pick(tr.metrics), "test": _pick(te.metrics),
                     "train_score": _score(tr.metrics, objective), "test_score": _score(te.metrics, objective)})
    if not rows:
        return {}
    rows.sort(key=lambda r: -r["train_score"])
    best = rows[0]
    default_params = strategy.get("defaults") or {}
    default_row = next((r for r in rows if r["params"] == default_params), None)
    ratio = (best["test_score"] / best["train_score"]) if best["train_score"] > 0 else 0
    if best["train_score"] <= 0:
        verdict = "no edge"
    else:
        verdict = ("robust" if ratio >= 0.6 and (best["test"].get("cagr") or 0) > 0 else
                   "fragile" if ratio > 0.2 else "over-fitted")
    return {"objective": objective, "train_period": [str(dates[0].date()), train_end],
            "test_period": [test_start, str(dates[-1].date())], "best": best, "default": default_row,
            "robustness_ratio": ratio, "verdict": verdict, "tested": len(rows),
            "table": rows[:25]}


def _pick(m: dict) -> dict:
    return {k: m.get(k) for k in ("cagr", "max_drawdown", "sharpe", "win_rate", "trades", "calmar")}
