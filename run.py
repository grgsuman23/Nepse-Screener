#!/usr/bin/env python3
"""NEPSE Screener command line.

  python run.py diagnose                 test every data source
  python run.py backfill [--limit 20]    download price history (first run)
  python run.py update                   append the latest trading day(s)
  python run.py fundamentals             refresh EPS / P/E / book value ...
  python run.py analyze                  indicators, scores, screens, backtests, tracker, dashboard data
  python run.py daily                    update + (weekly) fundamentals + analyze + e-mail
  python run.py intraday                 live prices every ~15 min during market hours (provisional)
  python run.py optimize [--strategy id] parameter search with out-of-sample test
  python run.py portfolio --file my.csv  print buy/hold/exit advice for a holdings file
  python run.py demo                     build the dashboard from synthetic demo data
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from screener import store
from screener.config import ROOT, get_paths, load_settings, load_strategies, materialize_strategy
from screener.utils import log, read_json, setup_logging, write_json


def cmd_analyze(cfg, paths, args, demo: bool = False):
    from screener import engine, screens, tracker
    from screener.backtest import Backtester, result_to_json
    from screener.export import export_all

    t0 = time.time()
    a = engine.build(cfg, paths)
    log.info("Analysis built: %d stocks × %d days (%.1fs)", len(a.symbols), len(a.dates), time.time() - t0)
    presets = screens.all_presets()
    hits = screens.run_presets(a.w, presets)
    # ---- strategies
    strategies_json, current = [], {}
    opt = read_json(paths.data / "optimization.json", {}) or {}
    if not getattr(args, "skip_backtest", False):
        bt = Backtester(a, cfg)
        for s in load_strategies():
            ms = materialize_strategy(s)
            try:
                t1 = time.time()
                r = bt.run(ms)
                j = result_to_json(r, int(cfg.get("export", {}).get("equity_curve_points", 400)))
                j["optimization"] = opt.get(s["id"])
                if a.fundamentals_mode == "current_backfill" and any(k in str(ms) for k in ("eps", "roe", "pe ", "score_value", "score_quality", "score")):
                    j["warnings"].append("Uses today's fundamentals for past dates (optimistic) until fundamental history builds up.")
                strategies_json.append(j)
                current[s["id"]] = r.current
                m = r.metrics
                log.info("  %-20s CAGR %6.1f%%  MaxDD %6.1f%%  trades %4s  win %4s  (%.1fs)", s["id"], m.get("cagr", 0) * 100,
                         m.get("max_drawdown", 0) * 100, m.get("trades", "-"),
                         f"{m['win_rate'] * 100:.0f}%" if m.get("win_rate") is not None else "-", time.time() - t1)
            except Exception as e:  # noqa: BLE001
                log.exception("Strategy %s failed: %s", s.get("id"), e)
    # ---- tracker (permanent ledgers)
    tracker.log_recommendations(a, paths, cfg)
    tracker.log_strategy_signals(a, paths, current)
    # ---- portfolios: CSV (file / PORTFOLIO_CSV secret) and Google Sheets (SHEET_PORTFOLIOS secret)
    from screener import sheets
    from screener.portfolio import analyze as pf_analyze, load_portfolio

    holdings_sets = []
    pf_src = getattr(args, "portfolio", None) or os.environ.get("PORTFOLIO_CSV")
    if pf_src:
        try:
            holdings_sets.append(("My portfolio", load_portfolio(pf_src)))
        except Exception as e:  # noqa: BLE001
            log.warning("Portfolio CSV could not be read: %s", e)
    holdings_sets += sheets.fetch_all(os.environ.get("SHEET_PORTFOLIOS"))
    portfolios = []
    for name, holdings in holdings_sets:
        try:
            res = pf_analyze(a, holdings, cfg)
            portfolios.append((name, res))
            if cfg.get("tracking", {}).get("log_portfolio_actions"):
                tracker.log_portfolio_actions(a, paths, res)
        except Exception as e:  # noqa: BLE001
            log.warning("Portfolio analysis failed for %s: %s", name, e)
    pf = portfolios[0][1] if portfolios else None
    tracking = tracker.evaluate_all(a, paths, cfg)
    export_all(a, paths, cfg, presets, hits, strategies_json, tracking, demo=demo)
    log.info("Dashboard data written to %s (%.1fs total)", paths.out, time.time() - t0)
    # ---- e-mail
    if cfg.get("alerts", {}).get("enabled") and not demo and not getattr(args, "no_email", False):
        from screener import alerts

        market = read_json(paths.out / "market.json")
        rows = read_json(paths.out / "screener.json")["rows"]
        top = sorted([r for r in rows if (r.get("stance_code") or 0) >= 1], key=lambda r: -(r.get("score") or 0))[:8]
        html = alerts.build_html(market, portfolios, strategies_json, top, tracking, os.environ.get("DASHBOARD_URL"))
        alerts.send(f"{cfg['alerts'].get('subject_prefix', '[NEPSE]')} {market['date']} · {market['regime_label']}", html)
    return a, pf


def cmd_intraday(cfg, paths, args):
    """During market hours: add today's live prices as a provisional bar and
    refresh the fast dashboard files (prices, scores, calls, market, sectors).
    Nothing is written to the stored price history or the track record."""
    from screener import engine, screens
    from screener.adjust import append_day
    from screener.export import export_all
    from screener.pipeline import NEPAL_TZ_OFFSET, Pipeline

    t0 = time.time()
    now = datetime.now(timezone.utc).replace(tzinfo=None) + NEPAL_TZ_OFFSET
    today = pd.Timestamp(now.date())
    pl = Pipeline(cfg, paths)
    if not (paths.data / "calibration.json").exists():
        pl.repair_dates()
    if pl.nepse is None:
        log.warning("NEPSE live feed unavailable — skipping intraday update")
        return
    is_open = pl.nepse.market_open()
    meta = read_json(paths.out / "meta.json", {}) or {}
    last_live = meta.get("live") or {}
    if is_open is False and not args.force and last_live.get("date") == today.strftime("%Y-%m-%d") and last_live.get("final"):
        log.info("Market closed and today's final intraday snapshot already published")
        return
    live = pl.nepse.live()
    if live.empty:
        log.info("No live prices (market closed or holiday)")
        return
    live = live.drop_duplicates("symbol").set_index("symbol")
    prices = store.load_all_prices(paths)
    n = 0
    for sym, hist in list(prices.items()):
        if sym not in live.index or len(hist) == 0 or hist["date"].max() > today:
            continue
        r = live.loc[sym].to_dict()
        bar = {"date": today, "close": r["close"], "volume": r.get("volume"), "turnover": r.get("turnover"),
               "prev_close": r.get("prev_close")}
        for k in ("open", "high", "low"):
            v = r.get(k)
            bar[k] = v if v == v and v else r["close"]
        prices[sym], _ = append_day(hist, bar, sym)
        n += 1
    idx = None
    try:
        il = pl.nepse.index_live()
        if il and il.get("close"):
            idx = pd.DataFrame([{"date": today, "open": il.get("prev_close") or il["close"], "high": il.get("high") or il["close"],
                                 "low": il.get("low") or il["close"], "close": il["close"]}])
    except Exception as e:  # noqa: BLE001
        log.warning("Live index unavailable: %r", e)
    log.info("Live prices for %d stocks (market %s)", n, "open" if is_open else "closed")
    a = engine.build(cfg, paths, prices=prices, index_extra=idx)
    presets = screens.all_presets()
    hits = screens.run_presets(a.w, presets)
    live_meta = {"date": today.strftime("%Y-%m-%d"), "as_of": now.strftime("%H:%M"), "market_open": bool(is_open),
                 "final": is_open is False, "stocks": n}
    export_all(a, paths, cfg, presets, hits, None, None, light=True, live=live_meta)
    pl.save_status()
    log.info("Intraday snapshot %s NPT written (%.0fs)", live_meta["as_of"], time.time() - t0)


def cmd_optimize(cfg, paths, args):
    from screener import engine
    from screener.backtest import Backtester
    from screener.optimize import optimize

    a = engine.build(cfg, paths)
    bt = Backtester(a, cfg)
    res = read_json(paths.data / "optimization.json", {}) or {}
    for s in load_strategies():
        if args.strategy and s["id"] != args.strategy:
            continue
        if not s.get("params"):
            continue
        log.info("Optimising %s ...", s["id"])
        o = optimize(bt, s, cfg)
        if o:
            o["run"] = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            res[s["id"]] = o
            b = o["best"]
            log.info("  best %s  train CAGR %.1f%% -> test CAGR %.1f%%  [%s]", b["params"], (b["train"]["cagr"] or 0) * 100,
                     (b["test"]["cagr"] or 0) * 100, o["verdict"])
    write_json(paths.data / "optimization.json", res)


def cmd_portfolio(cfg, paths, args):
    from screener import engine
    from screener.portfolio import analyze, load_portfolio

    a = engine.build(cfg, paths)
    pf = analyze(a, load_portfolio(args.file), cfg)
    s = pf["summary"]
    print(f"\nPortfolio {s['date']}: value Rs {s['value']:,.0f}  P&L Rs {s['pnl']:,.0f}  ({(s['pnl_pct'] or 0) * 100:+.1f}%)")
    print(s["regime_advice"], "\n")
    for p in pf["positions"]:
        print(f"{p['action']:<7} {p['symbol']:<9} LTP {p['ltp']:>9,.1f}  P&L {(p['pnl_pct'] or 0) * 100:+6.1f}%  "
              f"stop {p['stop'] or '-':>8}  {p['action_detail']}")
        for n in p["notes"]:
            print(" " * 10, "·", n)
    if pf["candidates"]:
        print("\nIdeas for new money:", ", ".join(f"{c['symbol']} ({c['stance']}, {c['score']:.0f})" for c in pf["candidates"]))


def fundamentals_stale(paths, days: int) -> bool:
    f = paths.fundamentals / "latest.csv"
    if not f.exists():
        return True
    return (time.time() - f.stat().st_mtime) > days * 86400 or len(list(paths.snapshots.glob("*.csv"))) == 0 or \
        (date.today() - max(date.fromisoformat(p.stem) for p in paths.snapshots.glob("*.csv"))).days >= days


def main(argv=None):
    p = argparse.ArgumentParser(description="NEPSE technical + fundamental screener")
    p.add_argument("command", choices=["diagnose", "universe", "backfill", "update", "fundamentals", "analyze",
                                       "daily", "intraday", "optimize", "portfolio", "demo"])
    p.add_argument("--limit", type=int, help="only process the first N symbols (testing)")
    p.add_argument("--symbols", help="comma-separated symbols")
    p.add_argument("--force", action="store_true")
    p.add_argument("--strategy")
    p.add_argument("--file", help="portfolio CSV for the portfolio command")
    p.add_argument("--portfolio", help="portfolio CSV used during analyze/daily (or env PORTFOLIO_CSV)")
    p.add_argument("--skip-backtest", action="store_true")
    p.add_argument("--no-email", action="store_true")
    p.add_argument("--root", help="project root (default: this folder)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    setup_logging(args.verbose)
    cfg = load_settings()
    if args.command == "demo":
        from tools.make_demo_data import build_demo

        root = Path(args.root or ROOT / "demo")
        build_demo(root, cfg)
        os.environ["SCREENER_ROOT"] = str(root)
        paths = get_paths(root)
        args.portfolio = args.portfolio or str(root / "demo_portfolio.csv")
        args.strategy = None
        cmd_optimize(cfg, paths, args)
        cmd_analyze(cfg, paths, args, demo=True)
        return
    paths = get_paths(args.root)
    syms = args.symbols.split(",") if args.symbols else None
    from screener.pipeline import Pipeline

    pl = Pipeline(cfg, paths)
    if args.command == "diagnose":
        pl.diagnose()
    elif args.command == "universe":
        pl.build_universe()
    elif args.command == "backfill":
        if store.read_universe(paths).empty or args.force:
            pl.build_universe()
        pl.backfill(syms, force=args.force, limit=args.limit)
    elif args.command == "update":
        pl.update_daily()
    elif args.command == "fundamentals":
        pl.update_fundamentals(syms, limit=args.limit)
    elif args.command == "analyze":
        cmd_analyze(cfg, paths, args)
    elif args.command == "daily":
        if store.read_universe(paths).empty:
            pl.build_universe()
        if not store.stored_symbols(paths):
            pl.backfill(limit=args.limit)
        else:
            if date.today().weekday() == 4:  # refresh the universe weekly (Friday) for new listings
                try:
                    pl.build_universe()
                    pl.backfill(limit=args.limit)  # new symbols only
                except Exception as e:  # noqa: BLE001
                    log.warning("Universe refresh failed: %s", e)
            pl.update_daily()
        if fundamentals_stale(paths, 7):
            pl.update_fundamentals(syms, limit=args.limit)
        cmd_analyze(cfg, paths, args)
    elif args.command == "intraday":
        cmd_intraday(cfg, paths, args)
    elif args.command == "optimize":
        cmd_optimize(cfg, paths, args)
    elif args.command == "portfolio":
        if not args.file:
            sys.exit("--file portfolio.csv is required")
        cmd_portfolio(cfg, paths, args)


if __name__ == "__main__":
    main()
