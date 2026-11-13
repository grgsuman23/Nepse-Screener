"""Data pipeline: universe -> price backfill -> daily update -> fundamentals.

Every step tries several sources in order and records which ones worked in
data/source_status.json (shown on the dashboard), so a broken website never
silently stops the screener."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import store
from .adjust import append_day, auto_adjust
from .config import ROOT, Paths
from .utils import Http, log, read_json, write_json

NEPAL_TZ_OFFSET = timedelta(hours=5, minutes=45)


def nepal_today() -> date:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + NEPAL_TZ_OFFSET).date()


def is_trading_weekday(d: date) -> bool:
    return d.weekday() in (6, 0, 1, 2, 3)  # Sun–Thu


class Pipeline:
    def __init__(self, cfg: dict, paths: Paths, config_dir: Path | None = None):
        self.cfg = cfg
        self.paths = paths
        self.config_dir = config_dir or ROOT / "config"
        dc = cfg.get("data", {})
        self.http = Http(delay=dc.get("request_delay_sec", 0.7), timeout=dc.get("request_timeout_sec", 25))
        self._nepse = None
        self._nepse_tried = False
        self.status = read_json(paths.status, {}) or {}

    # ------------------------------------------------------------ sources
    @property
    def nepse(self):
        if not self._nepse_tried:
            self._nepse_tried = True
            from .sources import nepse_api

            self._nepse = nepse_api.try_create()
        return self._nepse

    @property
    def mero(self):
        from .sources.merolagani import Merolagani

        return Merolagani(self.http)

    @property
    def sansar(self):
        from .sources.sharesansar import ShareSansar

        return ShareSansar(self.http)

    def _mark(self, source: str, ok: bool, detail: str = "") -> None:
        self.status[source] = {
            "ok": ok,
            "detail": detail[:300],
            "checked": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC"),
        }

    def save_status(self) -> None:
        write_json(self.paths.status, self.status)

    # ------------------------------------------------------------ universe
    def _exclude(self, df: pd.DataFrame) -> pd.DataFrame:
        dc = self.cfg.get("data", {})
        excl = {s.lower() for s in dc.get("exclude_sectors", [])}
        pats = [re.compile(p) for p in dc.get("exclude_symbol_patterns", [])]
        keep = ~df["sector"].fillna("").str.lower().isin(excl)
        keep &= ~df["symbol"].apply(lambda s: any(p.match(s) for p in pats))
        return df[keep].reset_index(drop=True)

    def build_universe(self) -> pd.DataFrame:
        uni = pd.DataFrame()
        if self.nepse is not None:
            try:
                uni = self.nepse.universe()
                self._mark("nepse_api:universe", True, f"{len(uni)} companies")
            except Exception as e:
                self._mark("nepse_api:universe", False, str(e))
        if uni.empty:
            existing = store.read_universe(self.paths)
            if len(existing):
                log.info("Using existing universe.csv (%d symbols)", len(existing))
                uni = existing
        if uni.empty:
            try:
                latest = self.mero.latest()
                uni = pd.DataFrame({"symbol": latest["symbol"], "name": "", "sector": "", "security_id": None})
                self._mark("merolagani:universe", True, f"{len(uni)} symbols (sector filled later)")
            except Exception as e:
                self._mark("merolagani:universe", False, str(e))
        if uni.empty:
            raise RuntimeError("Could not build a stock universe from any source")
        # keep sectors learnt from fundamentals if the source lacks them
        old = store.read_universe(self.paths)
        if len(old) and "sector" in old:
            m = old.set_index("symbol")["sector"].dropna().to_dict()
            uni["sector"] = [s if isinstance(s, str) and s else m.get(sym, "") for sym, s in zip(uni["symbol"], uni["sector"])]
        uni = self._exclude(uni)
        store.write_universe(self.paths, uni)
        log.info("Universe: %d equities", len(uni))
        return uni

    # ------------------------------------------------------------ history
    def fetch_history(self, symbol: str, start: date) -> tuple[pd.DataFrame, str]:
        th = self.cfg.get("data", {}).get("corporate_action_threshold", -0.11)
        for src in self.cfg.get("data", {}).get("history_sources", ["merolagani", "nepse_api"]):
            try:
                if src == "merolagani":
                    df = self.mero.history(symbol, start)
                    if len(df) > 5:
                        return df, src
                elif src == "nepse_api" and self.nepse is not None:
                    df = self.nepse.history(symbol, start)
                    if len(df) > 5:
                        df, events = auto_adjust(df, symbol, th)
                        store.append_corporate_actions(self.paths, events)
                        return df, src
            except Exception as e:
                log.debug("%s history failed for %s: %s", src, symbol, e)
                self._mark(f"{src}:history", False, f"{symbol}: {e}")
        return pd.DataFrame(), ""

    def backfill(self, symbols: list[str] | None = None, force: bool = False, limit: int | None = None) -> None:
        uni = store.read_universe(self.paths)
        if uni.empty:
            uni = self.build_universe()
        symbols = symbols or uni["symbol"].tolist()
        start = date.fromisoformat(self.cfg["data"].get("history_start", "2019-01-01"))
        have = set(store.stored_symbols(self.paths))
        todo = [s for s in symbols if force or s not in have]
        if limit:
            todo = todo[:limit]
        log.info("Backfilling %d symbols from %s", len(todo), start)
        ok = 0
        used: dict[str, int] = {}
        for i, sym in enumerate(todo, 1):
            df, src = self.fetch_history(sym, start)
            if len(df):
                store.write_prices(self.paths, sym, df)
                ok += 1
                used[src] = used.get(src, 0) + 1
            if i % 25 == 0:
                log.info("  %d/%d done (%d ok)", i, len(todo), ok)
        for src, n in used.items():
            self._mark(f"{src}:history", True, f"{n} symbols backfilled")
        log.info("Backfill complete: %d/%d symbols", ok, len(todo))
        self.update_index(start)
        self.save_status()

    # ------------------------------------------------------------ index
    def update_index(self, start: date | None = None) -> None:
        start = start or date.fromisoformat(self.cfg["data"].get("history_start", "2019-01-01"))
        name = self.cfg.get("regime", {}).get("index_symbol", "NEPSE")
        try:
            df = self.mero.history(name, start)
            if len(df) > 20:
                store.write_index(self.paths, df[["date", "open", "high", "low", "close", "volume"]], name)
                self._mark("merolagani:index", True, f"{len(df)} bars")
                return
        except Exception as e:
            self._mark("merolagani:index", False, str(e))
        if self.nepse is not None:
            try:
                df = self.nepse.index_history()
                if len(df) > 20:
                    old = store.read_index(self.paths, name)
                    df = pd.concat([old, df]).drop_duplicates("date", keep="last") if len(old) else df
                    store.write_index(self.paths, df, name)
                    self._mark("nepse_api:index", True, f"{len(df)} bars")
                    return
            except Exception as e:
                self._mark("nepse_api:index", False, str(e))
        log.warning("Index history unavailable — a computed market index will be used")

    # ------------------------------------------------------------ daily
    def _apply_day(self, day_df: pd.DataFrame, d: date, symbols: set[str]) -> int:
        n = 0
        all_events = []
        for rec in day_df.to_dict("records"):
            sym = rec.get("symbol")
            if sym not in symbols:
                continue
            rec["date"] = pd.Timestamp(rec.get("date") or d)
            for k in ("open", "high", "low"):
                if not rec.get(k) or rec.get(k) != rec.get(k):
                    rec[k] = rec["close"]
            hist = store.read_prices(self.paths, sym)
            hist, ev = append_day(hist, rec, sym)
            all_events += ev
            store.write_prices(self.paths, sym, hist)
            n += 1
        store.append_corporate_actions(self.paths, all_events)
        return n

    def update_daily(self, days_back: int = 10) -> None:
        uni = store.read_universe(self.paths)
        symbols = set(uni["symbol"]) if len(uni) else set(store.stored_symbols(self.paths))
        today = nepal_today()
        # what is the latest stored trading date?
        last_dates = []
        for s in list(symbols)[:40]:
            h = store.read_prices(self.paths, s)
            if len(h):
                last_dates.append(h["date"].max().date())
        last = max(last_dates) if last_dates else today - timedelta(days=days_back)
        wanted = [last + timedelta(days=i) for i in range(1, (today - last).days + 1)]
        wanted = [d for d in wanted if is_trading_weekday(d)]
        if not wanted:
            log.info("Prices already up to date (%s)", last)
        sources = self.cfg.get("data", {}).get("daily_sources", ["nepse_api", "merolagani_latest", "merolagani"])
        for d in wanted:
            done = False
            for src in sources:
                try:
                    if src == "nepse_api" and self.nepse is not None:
                        day = self.nepse.day(d)
                        if len(day) > 20 and (day["date"].max().date() == d):
                            n = self._apply_day(day, d, symbols)
                            self._mark("nepse_api:daily", True, f"{d}: {n} stocks")
                            done = True
                    elif src == "merolagani_latest" and d == today:
                        day = self.mero.latest()
                        if len(day) > 20:
                            day["date"] = pd.Timestamp(d)
                            n = self._apply_day(day, d, symbols)
                            self._mark("merolagani:latest", True, f"{d}: {n} stocks")
                            done = True
                    elif src == "merolagani":
                        n = self._refresh_recent_from_history(symbols, d - timedelta(days=20))
                        self._mark("merolagani:history", True, f"refreshed {n} stocks")
                        done = n > 0
                except Exception as e:
                    self._mark(f"{src}:daily", False, f"{d}: {e}")
                    log.warning("%s failed for %s: %s", src, d, e)
                if done:
                    break
            if not done:
                log.info("No data for %s (holiday or sources down)", d)
            if done and src == "merolagani":
                break  # history refresh already covered all remaining days
        self.update_index()
        self.save_status()

    def _refresh_recent_from_history(self, symbols: set[str], start: date) -> int:
        n = 0
        for sym in sorted(symbols):
            try:
                new = self.mero.history(sym, start)
            except Exception:
                continue
            if new.empty:
                continue
            hist = store.read_prices(self.paths, sym)
            merged = pd.concat([hist[hist["date"] < new["date"].min()], new], ignore_index=True)
            store.write_prices(self.paths, sym, merged)
            n += 1
        return n

    # ------------------------------------------------------------ fundamentals
    def update_fundamentals(self, symbols: list[str] | None = None, limit: int | None = None) -> pd.DataFrame:
        uni = store.read_universe(self.paths)
        symbols = symbols or uni["symbol"].tolist()
        if limit:
            symbols = symbols[:limit]
        old = store.read_fundamentals(self.paths)
        old_map = {r["symbol"]: r for r in old.to_dict("records")} if len(old) else {}
        rows, ok_by = [], {}
        srcs = self.cfg.get("data", {}).get("fundamental_sources", ["merolagani", "sharesansar"])
        for i, sym in enumerate(symbols, 1):
            rec = None
            for src in srcs:
                try:
                    data = (self.mero if src == "merolagani" else self.sansar).fundamentals(sym)
                    if data.get("eps") == data.get("eps") and ("eps" in data or "pe" in data):
                        rec = data
                        ok_by[src] = ok_by.get(src, 0) + 1
                        break
                except Exception as e:
                    log.debug("%s fundamentals failed for %s: %s", src, sym, e)
            if rec is None and sym in old_map:
                rec = dict(old_map[sym])
                rec["source"] = f"{rec.get('source', '')} (stale)"
            if rec is None:
                continue
            rec["symbol"] = sym
            rec.setdefault("as_of", nepal_today().isoformat())
            rows.append(rec)
            if i % 25 == 0:
                log.info("  fundamentals %d/%d", i, len(symbols))
        df = pd.DataFrame(rows)
        for c in store.FUND_COLS:
            if c not in df:
                df[c] = None
        df = df[store.FUND_COLS]
        # manual overrides (e.g. NPL, CAR from NRB quarterly reports)
        man = store.read_manual_fundamentals(self.config_dir)
        if len(man):
            df = df.set_index("symbol")
            man = man.set_index("symbol")
            for col in man.columns:
                if col not in df:
                    df[col] = None
                vals = man[col].dropna()
                df.loc[df.index.intersection(vals.index), col] = vals[vals.index.isin(df.index)]
            df = df.reset_index()
        for src, n in ok_by.items():
            self._mark(f"{src}:fundamentals", True, f"{n} companies")
        if not ok_by:
            self._mark("fundamentals", False, "no source returned data")
        if len(df):
            store.write_fundamentals(self.paths, df, nepal_today())
            # learn sectors for the universe file
            if len(uni):
                sec = df.set_index("symbol")["sector"].dropna().to_dict()
                uni["sector"] = [s if isinstance(s, str) and s else sec.get(sym, "") for sym, s in zip(uni["symbol"], uni["sector"])]
                store.write_universe(self.paths, uni)
        self.save_status()
        log.info("Fundamentals saved for %d companies", len(df))
        return df

    # ------------------------------------------------------------ diagnose
    def diagnose(self, symbol: str = "NABIL") -> dict:
        report = {}

        def run(name, fn):
            try:
                res = fn()
                report[name] = {"ok": True, "result": res}
            except Exception as e:
                report[name] = {"ok": False, "result": f"{type(e).__name__}: {e}"}
            self._mark(name, report[name]["ok"], str(report[name]["result"]))
            log.info("%-26s %s  %s", name, "OK  " if report[name]["ok"] else "FAIL", str(report[name]["result"])[:150])

        start = nepal_today() - timedelta(days=40)
        run("nepse_api:connect", lambda: "connected" if self.nepse is not None else (_ for _ in ()).throw(RuntimeError("library failed to start")))
        if self.nepse is not None:
            run("nepse_api:universe", lambda: f"{len(self.nepse.universe())} companies")
            run("nepse_api:history", lambda: f"{len(self.nepse.history(symbol, start))} bars for {symbol}")
            run("nepse_api:daily", lambda: f"{len(self.nepse.day(nepal_today() - timedelta(days=1)))} rows")
        run("merolagani:history", lambda: f"{len(self.mero.history(symbol, start))} bars for {symbol}")
        run("merolagani:index", lambda: f"{len(self.mero.history('NEPSE', start))} bars for NEPSE")
        run("merolagani:latest", lambda: f"{len(self.mero.latest())} rows")
        run("merolagani:fundamentals", lambda: self.mero.fundamentals(symbol))
        run("sharesansar:fundamentals", lambda: self.sansar.fundamentals(symbol))
        self.save_status()
        return report
