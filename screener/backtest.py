"""NEPSE-realistic portfolio backtester.

Models what actually happens when you trade on NEPSE:
* signals on day t's close are executed at day t+1's open (or close);
* tiered broker commission + SEBON fee on both sides, DP charge on sells;
* capital-gains tax on each profitable sale (short vs long-term rate);
* T+2 settlement — sale proceeds can't be reused for two trading days;
* no short selling; orders of at least the minimum lot (10 kitta);
* stocks locked at upper circuit can't be bought, at lower circuit can't be sold;
* order size capped at a share of the day's traded volume (liquidity);
* stocks that don't trade on a day can't be traded that day.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .costs import Costs
from .dsl import Evaluator
from .engine import Analysis


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    qty: int
    cost_basis: float
    exit_date: str | None = None
    exit_price: float | None = None
    net_proceeds: float | None = None
    tax: float | None = None
    pnl: float | None = None
    ret: float | None = None
    hold_days: int | None = None
    reason: str = ""


@dataclass
class Result:
    strategy: dict
    equity: pd.Series
    benchmark: pd.Series
    exposure: pd.Series
    trades: list[Trade]
    open_positions: list[dict]
    metrics: dict
    bench_metrics: dict
    yearly: list[dict]
    current: dict
    warnings: list[str]


def _period_key(d: pd.Timestamp, rebalance: str):
    if rebalance == "weekly":
        # NEPSE week starts on Sunday
        t = (d + pd.Timedelta(days=1)).date()
        return tuple(t.isocalendar())[:2]
    if rebalance == "quarterly":
        return (d.year, (d.month - 1) // 3)
    return (d.year, d.month)


def metrics(equity: pd.Series, trades: list[Trade] | None, cfg: dict, exposure: pd.Series | None = None) -> dict:
    eq = equity.dropna()
    if len(eq) < 2:
        return {}
    tdy = cfg.get("market", {}).get("trading_days_per_year", 230)
    rf = cfg.get("backtest", {}).get("risk_free_rate", 0.05)
    rets = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1
    vol = rets.std() * math.sqrt(tdy)
    ex = rets - rf / tdy
    sharpe = ex.mean() / rets.std() * math.sqrt(tdy) if rets.std() > 0 else 0
    down = rets[rets < 0].std()
    sortino = ex.mean() / down * math.sqrt(tdy) if down and down > 0 else 0
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    # longest drawdown (days)
    under = dd < 0
    longest, cur = 0, 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    m = {
        "total_return": total, "cagr": cagr, "volatility": vol, "sharpe": sharpe, "sortino": sortino,
        "max_drawdown": mdd, "longest_drawdown_days": longest,
        "calmar": cagr / abs(mdd) if mdd < 0 else 0, "final_equity": eq.iloc[-1],
        "start": eq.index[0].strftime("%Y-%m-%d"), "end": eq.index[-1].strftime("%Y-%m-%d"),
    }
    if exposure is not None and len(exposure):
        m["exposure"] = float(exposure.mean())
    if trades is not None:
        closed = [t for t in trades if t.exit_date]
        m["trades"] = len(closed)
        if closed:
            r = np.array([t.ret for t in closed])
            pnl = np.array([t.pnl for t in closed])
            wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
            m["win_rate"] = float((pnl > 0).mean())
            m["avg_return"] = float(r.mean())
            m["avg_win"] = float(r[r > 0].mean()) if (r > 0).any() else 0.0
            m["avg_loss"] = float(r[r <= 0].mean()) if (r <= 0).any() else 0.0
            m["profit_factor"] = float(wins.sum() / abs(losses.sum())) if losses.sum() < 0 else float("inf") if wins.sum() > 0 else 0.0
            m["expectancy"] = float(pnl.mean())
            m["avg_hold_days"] = float(np.mean([t.hold_days for t in closed]))
            m["total_tax"] = float(sum(t.tax or 0 for t in closed))
            m["best_trade"] = float(r.max())
            m["worst_trade"] = float(r.min())
    return m


def yearly_returns(equity: pd.Series, bench: pd.Series) -> list[dict]:
    out = []
    e = equity.groupby(equity.index.year).last()
    b = bench.groupby(bench.index.year).last()
    e0, b0 = equity.iloc[0], bench.iloc[0]
    prev_e, prev_b = e0, b0
    for y in e.index:
        out.append({"year": int(y), "strategy": e[y] / prev_e - 1, "benchmark": b.get(y, np.nan) / prev_b - 1})
        prev_e, prev_b = e[y], b.get(y, prev_b)
    return out


class Backtester:
    def __init__(self, analysis: Analysis, cfg: dict):
        self.a = analysis
        self.cfg = cfg
        self.costs = Costs(cfg)
        self.ev = Evaluator(analysis.w)

    def run(self, strategy: dict, start: str | None = None, end: str | None = None,
            initial_capital: float | None = None) -> Result:
        if strategy.get("type") == "blend":
            return self._run_blend(strategy, start, end, initial_capital)
        cfg, a, w = self.cfg, self.a, self.a.w
        bt = cfg.get("backtest", {})
        mk = cfg.get("market", {})
        capital = float(initial_capital or bt.get("initial_capital", 1_000_000))
        start = start or bt.get("start")
        end = end or bt.get("end")
        dates = a.dates
        if start:
            dates = dates[dates >= pd.Timestamp(start)]
        if end:
            dates = dates[dates <= pd.Timestamp(end)]
        if len(dates) < 30:
            raise ValueError("Backtest window too short")
        syms = a.symbols
        col = {s: j for j, s in enumerate(syms)}
        rows = a.dates.get_indexer(dates)

        def arr(name):
            return w[name].to_numpy(dtype=float)[rows]

        O, H, L, C, V = arr("open"), arr("high"), arr("low"), arr("close"), arr("volume")
        ATR = arr("atr_14")
        TR = w["traded"].to_numpy(dtype=float)[rows] > 0
        prevC = w["close"].shift(1).to_numpy(dtype=float)[rows]
        stype = strategy.get("type", "signal")
        warnings = []
        if stype == "signal":
            entry = self.ev.mask(strategy["entry"]).to_numpy()[rows]
            exit_ = self.ev.mask(strategy["exit"]).to_numpy()[rows] if strategy.get("exit") else np.zeros_like(entry)
            filt = None
        else:
            filt = self.ev.mask(strategy.get("filter") or "close > 0").to_numpy()[rows]
            hold_while = self.ev.mask(strategy["hold_while"]).to_numpy()[rows] if strategy.get("hold_while") else None
            entry = exit_ = None
        rank_expr = strategy.get("rank_by") or "rs_rating"
        rank = self.ev.eval(rank_expr)
        rank = (rank if isinstance(rank, pd.DataFrame) else w["close"] * 0 + rank).to_numpy(dtype=float)[rows]
        SIZE = None
        if strategy.get("size_by"):  # e.g. pos_size_combined: fraction of a normal position by regime
            sz = self.ev.eval(strategy["size_by"])
            SIZE = (sz if isinstance(sz, pd.DataFrame) else w["close"] * 0 + sz).to_numpy(dtype=float)[rows]
        reg_ok = None
        if strategy.get("regime_filter"):
            reg_ok = self.ev.mask(strategy["regime_filter"]).to_numpy()[rows].any(axis=1)

        limit = mk.get("circuit_limit", 0.10)
        settle = int(mk.get("settlement_days", 2))
        min_lot = int(mk.get("min_lot", 10))
        max_vol = cfg.get("liquidity", {}).get("max_pct_of_volume", 0.10)
        exec_open = bt.get("execution", "next_open") == "next_open"
        max_pos = int(strategy.get("max_positions") or strategy.get("top_n") or 8)
        stop_atr = strategy.get("stop_atr")
        stop_atr = float(stop_atr) if stop_atr not in (None, "", "null") else None
        stop_pct = strategy.get("stop_loss_pct")
        tp_pct = strategy.get("take_profit_pct")
        max_hold = strategy.get("max_hold_days")
        sizing = strategy.get("position_size", "equal")
        risk = float(strategy.get("risk_per_trade", 0.01))
        max_w = float(strategy.get("max_position_weight", cfg.get("portfolio", {}).get("max_position_weight", 0.2)))
        rebalance = strategy.get("rebalance", "monthly")
        sec_cap = strategy.get("max_per_sector")
        sec_cap = int(sec_cap) if sec_cap not in (None, "", "null") else None
        sec_of = [a.sector_of.get(x, "Others") for x in syms]

        def capped(order, held=()):
            """Best-ranked names, at most `max_per_sector` from any one sector."""
            if not sec_cap:
                return list(order[:max_pos])
            cnt, out = {}, []
            for j in order:
                k = sec_of[j]
                if cnt.get(k, 0) < sec_cap:
                    out.append(j)
                    cnt[k] = cnt.get(k, 0) + 1
                if len(out) >= max_pos:
                    break
            return out

        cash = capital
        unsettled: list[tuple[int, float]] = []
        pos: dict[int, dict] = {}       # col -> position
        pend_buy: list[tuple[int, str]] = []
        pend_sell: dict[int, str] = {}
        trades: list[Trade] = []
        eq_hist = np.zeros(len(dates))
        expo = np.zeros(len(dates))
        last_period = None
        prev_equity = capital
        wish: list[tuple[int, str]] = []   # rotation picks waiting for cash (T+2) / tradable day

        def price_at(i, j):
            p = O[i, j] if exec_open and O[i, j] > 0 else C[i, j]
            return p if p == p and p > 0 else np.nan

        for i, d in enumerate(dates):
            # 1) settlement
            if unsettled:
                still = []
                for avail, amt in unsettled:
                    if i >= avail:
                        cash += amt
                    else:
                        still.append((avail, amt))
                unsettled = still
            # 2) execute pending sells
            for j, reason in list(pend_sell.items()):
                if j not in pos:
                    pend_sell.pop(j)
                    continue
                if not TR[i, j]:
                    continue
                locked_down = H[i, j] == L[i, j] and prevC[i, j] > 0 and C[i, j] <= prevC[i, j] * (1 - limit * 0.95)
                if locked_down:
                    continue
                px = price_at(i, j)
                if px != px:
                    continue
                p = pos.pop(j)
                hold = (d - p["entry_date"]).days
                br = self.costs.sell_breakdown(p["qty"], px, p["cost_basis"], hold)
                unsettled.append((i + settle, br["net_proceeds"]))
                t = p["trade"]
                t.exit_date, t.exit_price = d.strftime("%Y-%m-%d"), round(px, 2)
                t.net_proceeds, t.tax = round(br["net_proceeds"], 2), round(br["cgt"], 2)
                t.pnl = round(br["net_proceeds"] - p["cost_basis"], 2)
                t.ret = br["net_proceeds"] / p["cost_basis"] - 1
                t.hold_days, t.reason = hold, reason
                trades.append(t)
                pend_sell.pop(j)
            # 3) execute pending buys
            if pend_buy:
                n_slots = max_pos - len(pos)
                budget_each = prev_equity / max_pos
                for j, why in pend_buy[: max(0, n_slots)]:
                    if j in pos or not TR[i, j]:
                        continue
                    locked_up = H[i, j] == L[i, j] and prevC[i, j] > 0 and C[i, j] >= prevC[i, j] * (1 + limit * 0.95)
                    if locked_up:
                        continue
                    px = price_at(i, j)
                    if px != px:
                        continue
                    if sizing == "atr_risk" and stop_atr and ATR[i, j] > 0:
                        target = (prev_equity * risk) / (stop_atr * ATR[i, j]) * px
                        target = min(target, prev_equity * max_w)
                    else:
                        target = min(budget_each, prev_equity * max_w)
                    if SIZE is not None:
                        f = SIZE[i - 1, j] if i > 0 else SIZE[i, j]
                        target *= f if f == f and f > 0 else 1.0
                    target = min(target, cash / (1 + self.costs.round_trip_pct(max(cash, 1)) / 2 + 0.001))
                    if V[i, j] > 0:
                        target = min(target, V[i, j] * max_vol * px)
                    qty = int(target // px)
                    if qty < min_lot:
                        continue
                    value = qty * px
                    fee = self.costs.buy_cost(value)
                    if value + fee > cash:
                        continue
                    cash -= value + fee
                    tr = Trade(symbol=syms[j], entry_date=d.strftime("%Y-%m-%d"), entry_price=round(px, 2), qty=qty,
                               cost_basis=round(value + fee, 2), reason=why)
                    pos[j] = {"qty": qty, "entry_price": px, "entry_date": d, "entry_i": i, "cost_basis": value + fee,
                              "high": C[i, j], "stop": (C[i, j] - stop_atr * ATR[i, j]) if stop_atr and ATR[i, j] == ATR[i, j] else None,
                              "trade": tr}
            if stype != "signal":
                wish = [x for x in wish if x[0] not in pos]
            pend_buy = []
            # 4) mark to market
            mv = 0.0
            for j, p in pos.items():
                c = C[i, j] if C[i, j] == C[i, j] else p["entry_price"]
                mv += p["qty"] * c
            equity = cash + sum(x[1] for x in unsettled) + mv
            eq_hist[i] = equity
            expo[i] = mv / equity if equity > 0 else 0
            prev_equity = equity
            if i == len(dates) - 1:
                break
            # 5) stops & exits (decided on today's close, executed tomorrow)
            for j, p in pos.items():
                if j in pend_sell:
                    continue
                c = C[i, j]
                if c != c:
                    continue
                p["high"] = max(p["high"], c)
                if stop_atr and ATR[i, j] == ATR[i, j]:
                    ns = p["high"] - stop_atr * ATR[i, j]
                    p["stop"] = ns if p["stop"] is None else max(p["stop"], ns)
                reason = None
                if stype == "signal" and exit_[i, j]:
                    reason = "exit rule"
                elif p["stop"] is not None and c < p["stop"]:
                    reason = f"ATR trailing stop ({stop_atr}×)"
                elif stop_pct and c <= p["entry_price"] * (1 - float(stop_pct)):
                    reason = f"stop loss {float(stop_pct):.0%}"
                elif tp_pct and c >= p["entry_price"] * (1 + float(tp_pct)):
                    reason = f"take profit {float(tp_pct):.0%}"
                elif max_hold and (i - p["entry_i"]) >= int(max_hold):
                    reason = f"time exit ({int(max_hold)} days)"
                if reason:
                    pend_sell[j] = reason
            # 6) new entries
            if stype == "signal":
                held_after = len(pos) - len(pend_sell)
                slots = max_pos - held_after
                if slots > 0:
                    cand = np.where(entry[i] & TR[i])[0]
                    cand = [j for j in cand if j not in pos]
                    if cand:
                        cand.sort(key=lambda j: -(rank[i, j] if rank[i, j] == rank[i, j] else -1e18))
                        pend_buy = [(j, "entry rule") for j in cand[:slots]]
            else:
                key = _period_key(d, rebalance)
                is_reb = last_period is None or key != last_period
                last_period = key
                if is_reb:
                    if reg_ok is not None and not reg_ok[i]:
                        wish = []
                        for j in pos:
                            pend_sell.setdefault(j, "regime filter → cash")
                    else:
                        elig = np.where(filt[i])[0]
                        elig = sorted(elig, key=lambda j: -(rank[i, j] if rank[i, j] == rank[i, j] else -1e18))
                        top = capped(elig)
                        keep = []
                        for j in pos:
                            if j in pend_sell:
                                continue
                            ok = (hold_while[i, j] if hold_while is not None else False) or j in top
                            if ok:
                                keep.append(j)
                            else:
                                pend_sell[j] = "rotated out"
                        keep = keep[:max_pos]
                        if sec_cap:  # held names count against the sector cap too
                            cnt = {}
                            for j in keep:
                                cnt[sec_of[j]] = cnt.get(sec_of[j], 0) + 1
                            new = []
                            for j in top:
                                if j in pos or cnt.get(sec_of[j], 0) >= sec_cap:
                                    continue
                                new.append(j)
                                cnt[sec_of[j]] = cnt.get(sec_of[j], 0) + 1
                            new = new[: max(0, max_pos - len(keep))]
                        else:
                            new = [j for j in top if j not in pos][: max(0, max_pos - len(keep))]
                        # sells settle T+2: unfilled picks are retried daily until the next rebalance
                        wish = [(j, "rebalance pick") for j in new]
                        pend_buy = list(wish)
                elif wish and (reg_ok is None or reg_ok[i]):
                    pend_buy = [x for x in wish if x[0] not in pos]

        equity = pd.Series(eq_hist, index=dates)
        bench = a.market["index"].reindex(dates).ffill().bfill()
        bench = bench / bench.iloc[0] * capital
        exposure = pd.Series(expo, index=dates)
        open_positions = []
        for j, p in pos.items():
            c = C[-1, j]
            hold = (dates[-1] - p["entry_date"]).days
            br = self.costs.sell_breakdown(p["qty"], c, p["cost_basis"], hold)
            open_positions.append({"symbol": syms[j], "entry_date": p["entry_date"].strftime("%Y-%m-%d"),
                                   "entry_price": round(p["entry_price"], 2), "qty": p["qty"], "last": round(c, 2),
                                   "unrealized_ret": br["net_proceeds"] / p["cost_basis"] - 1,
                                   "stop": round(p["stop"], 2) if p["stop"] else None})
        m = metrics(equity, trades, cfg, exposure)
        bm = metrics(bench, None, cfg)
        m["excess_cagr"] = m.get("cagr", 0) - bm.get("cagr", 0)
        cur = self.current_signals(strategy, dates[-1])
        return Result(strategy=strategy, equity=equity, benchmark=bench, exposure=exposure, trades=trades,
                      open_positions=open_positions, metrics=m, bench_metrics=bm,
                      yearly=yearly_returns(equity, bench), current=cur, warnings=warnings)

    # ------------------------------------------------------------------ blend
    @staticmethod
    def resolve_sleeves(strategy: dict) -> list[dict]:
        """Sleeves of a blend: [{label, weight, strategy}] with `use:` resolved
        against strategies.yaml and `overrides:` applied on top."""
        from screener.config import load_strategies, materialize_strategy

        lib = {s["id"]: s for s in load_strategies()}
        out = []
        for sl in strategy.get("sleeves") or []:
            base = lib.get(sl.get("use")) if sl.get("use") else sl.get("strategy")
            if base is None:
                raise ValueError(f"Blend {strategy.get('id')}: unknown sleeve strategy {sl.get('use')!r}")
            if base.get("type") == "blend":
                raise ValueError("A blend cannot contain another blend")
            ms = materialize_strategy(base)
            ms.update(sl.get("overrides") or {})
            out.append({"label": sl.get("label") or ms.get("name") or sl.get("use"), "weight": float(sl.get("weight", 0)),
                        "use": sl.get("use"), "strategy": ms})
        tot = sum(x["weight"] for x in out)
        if not out or tot <= 0:
            raise ValueError(f"Blend {strategy.get('id')}: no sleeves with weight")
        for x in out:
            x["weight"] /= tot
        return out

    def _run_blend(self, strategy: dict, start, end, initial_capital) -> Result:
        """Several strategies run side by side on their share of the capital and
        brought back to the target weights at each rebalance (default quarterly).
        Each sleeve trades with its own costs, tax, T+2 and liquidity limits; the
        rebalance itself is modelled as a cash transfer between sleeves."""
        cfg = self.cfg
        capital = float(initial_capital or cfg.get("backtest", {}).get("initial_capital", 1_000_000))
        sleeves = self.resolve_sleeves(strategy)
        runs = [self.run(sl["strategy"], start=start, end=end, initial_capital=capital * sl["weight"]) for sl in sleeves]
        idx = runs[0].equity.index
        R = np.column_stack([r.equity.reindex(idx).ffill().pct_change().fillna(0).to_numpy() for r in runs])
        X = np.column_stack([r.exposure.reindex(idx).fillna(0).to_numpy() for r in runs])
        w = np.array([sl["weight"] for sl in sleeves])
        val = capital * w
        reb = strategy.get("rebalance", "quarterly")
        eq, ex, vals = np.zeros(len(idx)), np.zeros(len(idx)), np.zeros((len(idx), len(w)))
        last = None
        for i, d in enumerate(idx):
            key = _period_key(d, reb)
            if last is not None and key != last:
                val = val.sum() * w
            last = key
            val = val * (1 + R[i])
            eq[i], vals[i] = val.sum(), val
            ex[i] = float((val * X[i]).sum() / val.sum()) if val.sum() > 0 else 0.0
        equity = pd.Series(eq, index=idx)
        exposure = pd.Series(ex, index=idx)
        bench = runs[0].benchmark
        trades = []
        for sl, r in zip(sleeves, runs):
            for t in r.trades:
                t2 = Trade(**{**asdict(t), "reason": f"[{sl['label']}] {t.reason}"})
                trades.append(t2)
        trades.sort(key=lambda t: (t.exit_date or "", t.entry_date))
        open_positions = []
        for k, (sl, r) in enumerate(zip(sleeves, runs)):
            scale = vals[-1, k] / r.equity.iloc[-1] if r.equity.iloc[-1] > 0 else 1.0
            for p in r.open_positions:
                open_positions.append({**p, "qty": int(p["qty"] * scale), "sleeve": sl["label"]})
        m = metrics(equity, trades, cfg, exposure)
        bm = metrics(bench, None, cfg)
        m["excess_cagr"] = m.get("cagr", 0) - bm.get("cagr", 0)
        m["sleeves"] = [{"label": sl["label"], "use": sl["use"], "target_weight": round(sl["weight"], 3),
                         "weight_now": round(float(vals[-1, k] / vals[-1].sum()), 3),
                         "cagr": r.metrics.get("cagr"), "max_drawdown": r.metrics.get("max_drawdown")}
                        for k, (sl, r) in enumerate(zip(sleeves, runs))]
        cur = {"date": idx[-1].strftime("%Y-%m-%d"), "entries": [], "exits": [], "picks": [], "sleeves": []}
        seen = set()
        for sl, r in zip(sleeves, runs):
            c = r.current or {}
            names = c.get("picks") or c.get("entries") or []
            cur["sleeves"].append({"label": sl["label"], "weight": round(sl["weight"], 3), "picks": names,
                                   "regime_ok": c.get("regime_ok", True)})
            for n in names:
                if n not in seen:
                    seen.add(n)
                    cur["picks"].append(n)
        return Result(strategy=strategy, equity=equity, benchmark=bench, exposure=exposure, trades=trades,
                      open_positions=open_positions, metrics=m, bench_metrics=bm,
                      yearly=yearly_returns(equity, bench), current=cur, warnings=[w for r in runs for w in r.warnings])

    def current_signals(self, strategy: dict, at: pd.Timestamp) -> dict:
        """What the strategy says *today* (for the dashboard & tracker)."""
        w = self.a.w
        window = {k: v.loc[:at].iloc[-300:] for k, v in w.items()}
        ev = Evaluator(window)
        rank = ev.eval(strategy.get("rank_by") or "rs_rating")
        rank_row = rank.iloc[-1] if isinstance(rank, pd.DataFrame) else pd.Series(rank, index=self.a.symbols)
        out = {"date": at.strftime("%Y-%m-%d"), "entries": [], "exits": [], "picks": []}
        if strategy.get("type", "signal") == "signal":
            ent = ev.mask(strategy["entry"]).iloc[-1]
            names = ent[ent].index.tolist()
            out["entries"] = sorted(names, key=lambda s: -(rank_row.get(s, 0) if rank_row.get(s, 0) == rank_row.get(s, 0) else 0))
            if strategy.get("exit"):
                ex = ev.mask(strategy["exit"]).iloc[-1]
                out["exits"] = sorted(ex[ex].index.tolist())
        else:
            ok = True
            if strategy.get("regime_filter"):
                ok = bool(ev.mask(strategy["regime_filter"]).iloc[-1].any())
            f = ev.mask(strategy.get("filter") or "close > 0").iloc[-1]
            elig = f[f].index.tolist()
            elig.sort(key=lambda s: -(rank_row.get(s, 0) if rank_row.get(s, 0) == rank_row.get(s, 0) else 0))
            n, cap = int(strategy.get("top_n") or 8), strategy.get("max_per_sector")
            if cap not in (None, "", "null"):
                cnt, sel = {}, []
                for s_ in elig:
                    k = self.a.sector_of.get(s_, "Others")
                    if cnt.get(k, 0) < int(cap):
                        sel.append(s_)
                        cnt[k] = cnt.get(k, 0) + 1
                    if len(sel) >= n:
                        break
                elig = sel
            out["picks"] = elig[:n] if ok else []
            out["regime_ok"] = ok
        return out


def result_to_json(r: Result, points: int = 400) -> dict:
    step = max(1, len(r.equity) // points)
    eq = r.equity.iloc[::step]
    if eq.index[-1] != r.equity.index[-1]:
        eq = pd.concat([eq, r.equity.iloc[-1:]])
    bm = r.benchmark.reindex(eq.index)
    dd = (r.equity / r.equity.cummax() - 1).reindex(eq.index)
    s = r.strategy
    return {
        "id": s.get("id"), "name": s.get("name"), "type": s.get("type", "signal"),
        "description": (s.get("description") or "").strip(),
        "rules": {k: s.get(k) for k in ("entry", "exit", "filter", "rank_by", "hold_while", "regime_filter",
                                         "rebalance", "top_n", "max_per_sector", "max_positions", "stop_atr", "stop_loss_pct", "size_by",
                                         "take_profit_pct", "max_hold_days") if s.get(k) not in (None, "")}
                 | ({"sleeves": "; ".join(f"{x.get('label') or x.get('use')} {float(x.get('weight', 0)):.0%}"
                                          + (f" ({x['use']}" + (", " + ", ".join(f"{k}={v}" for k, v in (x.get('overrides') or {}).items()) if x.get('overrides') else "") + ")" if x.get('use') else "")
                                          for x in s.get("sleeves") or [])} if s.get("sleeves") else {}),
        "params": s.get("_params", {}),
        "metrics": r.metrics, "benchmark_metrics": r.bench_metrics, "yearly": r.yearly,
        "curve": {"dates": [d.strftime("%Y-%m-%d") for d in eq.index], "equity": eq.round(0).tolist(),
                  "benchmark": bm.round(0).tolist(), "drawdown": dd.round(4).tolist()},
        "trades": [asdict(t) for t in r.trades[-150:]][::-1],
        "open_positions": r.open_positions, "current": r.current, "warnings": r.warnings,
    }
