"""Builds the full analysis panel.

Everything is held as *wide* DataFrames (dates × symbols), one per field, in
`Analysis.w`. The strategy DSL, backtester, scoring and tracker all work on
these, so a rule like `close > sma_200 and rsi_14 < 40` is evaluated for
every stock on every day in one vectorised step."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import fundamentals as fmod
from . import market as mkt
from . import scoring, store
from .config import Paths
from .indicators import compute
from .utils import log


@dataclass
class Analysis:
    w: dict[str, pd.DataFrame]
    market: pd.DataFrame
    sectors: dict[str, pd.DataFrame]
    universe: pd.DataFrame
    sector_of: dict[str, str]
    name_of: dict[str, str]
    last_traded: pd.Series
    fundamentals_mode: str
    index_source: str
    warnings: list[str] = field(default_factory=list)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.w["close"].index

    @property
    def symbols(self) -> list[str]:
        return list(self.w["close"].columns)

    def latest(self, at: pd.Timestamp | None = None) -> pd.DataFrame:
        """Cross-section of every field on one date (default: last date)."""
        at = at if at is not None else self.dates[-1]
        rows = {k: v.loc[at] for k, v in self.w.items() if isinstance(v, pd.DataFrame)}
        df = pd.DataFrame(rows)
        df.index.name = "symbol"
        df["sector"] = [self.sector_of.get(s, "Others") for s in df.index]
        df["name"] = [self.name_of.get(s, "") for s in df.index]
        df["last_traded"] = self.last_traded.reindex(df.index)
        return df


PRICE_FIELDS = ["open", "high", "low", "close", "volume", "turnover"]


def build(cfg: dict, paths: Paths, prices: dict[str, pd.DataFrame] | None = None,
          start: str | None = None) -> Analysis:
    warnings: list[str] = []
    uni = store.read_universe(paths)
    prices = prices if prices is not None else store.load_all_prices(paths, uni["symbol"].tolist() if len(uni) else None)
    min_bars = cfg.get("data", {}).get("min_history_days", 60)
    if not prices:
        raise RuntimeError("No price data found — run `python run.py backfill` first")

    # --- indicators per stock (on its own traded bars)
    ind = {}
    for sym, df in prices.items():
        if len(df) < 30:
            continue
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        d = d.drop_duplicates("date", keep="last").set_index("date").sort_index()
        if start:
            d = d[d.index >= pd.Timestamp(start) - pd.Timedelta(days=420)]
        if len(d) < 30:
            continue
        ind[sym] = compute(d)
    if not ind:
        raise RuntimeError("Not enough price history to analyse")
    log.info("Indicators computed for %d stocks", len(ind))

    big = pd.concat(ind, axis=1, sort=True).sort_index()
    calendar = big.index
    fields = list(next(iter(ind.values())).columns)
    symbols = sorted(ind)
    w: dict[str, pd.DataFrame] = {}
    for f in fields:
        x = big.xs(f, axis=1, level=1).reindex(columns=symbols)
        w[f] = x
    traded = w["volume"].notna() & (w["volume"] > 0)
    last_traded = pd.Series({s: ind[s].index.max() for s in symbols})
    for f in fields:
        if f in ("volume", "turnover", "chg_pct"):
            w[f] = w[f].fillna(0.0)
        else:
            w[f] = w[f].ffill(limit=15)
    w["traded"] = traded.astype(float)

    # --- liquidity & relative strength
    lq = cfg.get("liquidity", {}).get("min_avg_turnover", 2_000_000)
    recent_trade = traded.astype(float).rolling(10, min_periods=1).max() > 0
    liquid = (w["turnover_sma_20"] >= lq) & recent_trade
    w["liquid"] = liquid.astype(float)
    blend = 0.4 * w["ret_3m"] + 0.3 * w["ret_6m"].fillna(w["ret_3m"]) + 0.3 * w["ret_12m"].fillna(w["ret_6m"]).fillna(w["ret_3m"])
    w["rs_rating"] = (blend.rank(axis=1, pct=True) * 98 + 1).round(1)

    # --- market index & regime
    idx = store.read_index(paths, cfg.get("regime", {}).get("index_symbol", "NEPSE"))
    index_source = "official"
    if len(idx) > 200:
        index_close = idx.set_index("date")["close"].reindex(calendar).ffill()
        if index_close.isna().mean() > 0.3:
            index_close = None
    else:
        index_close = None
    if index_close is None:
        index_close = mkt.computed_index(w["close"], liquid)
        index_source = "computed (equal-weight of liquid stocks)"
        warnings.append("Official NEPSE index history unavailable — regime uses a computed equal-weight index.")
    br = mkt.breadth(w["close"], w)
    market = mkt.regime_frame(index_close, br, cfg)

    def broadcast(series: pd.Series) -> pd.DataFrame:
        return pd.DataFrame(np.repeat(series.to_numpy()[:, None], len(symbols), axis=1), index=calendar, columns=symbols)

    for col in ("regime", "breadth_50", "breadth_200", "mkt_ret_1m", "mkt_ret_3m", "mkt_ret_6m"):
        w[col] = broadcast(market[col])
    w["rs_vs_mkt_3m"] = w["ret_3m"] - w["mkt_ret_3m"]

    # --- sectors
    sector_of, name_of = {}, {}
    if len(uni):
        sector_of = {r["symbol"]: (r.get("sector") if isinstance(r.get("sector"), str) and r.get("sector") else "Others")
                     for r in uni.to_dict("records")}
        name_of = {r["symbol"]: (r.get("name") if isinstance(r.get("name"), str) else "") for r in uni.to_dict("records")}
    fund_latest = store.read_fundamentals(paths)
    if len(fund_latest) and "sector" in fund_latest:
        for r in fund_latest.to_dict("records"):
            if (not sector_of.get(r["symbol"]) or sector_of.get(r["symbol"]) == "Others") and isinstance(r.get("sector"), str):
                sector_of[r["symbol"]] = r["sector"]
    for s in symbols:
        sector_of.setdefault(s, "Others")
    w["sector_rs_rank"], sectors = mkt.sector_analysis(w["close"], sector_of, market["index"], liquid)

    # --- fundamentals
    snaps = store.load_snapshots(paths)
    mode = fmod.choose_mode(snaps, calendar[-1], cfg.get("backtest", {}).get("fundamentals_mode", "auto"))
    if len(fund_latest) == 0 and (snaps is None or snaps.empty):
        warnings.append("No fundamentals yet — run `python run.py fundamentals`. Value/quality factors are skipped.")
    else:
        base = fmod.wide_base_fields(symbols, calendar, fund_latest, snaps, mode)
        ff = fmod.derive(w["close"], base, cfg.get("market", {}).get("face_value", 100))
        w.update(ff)
        w.update(fmod.sector_relative(ff, sector_of))
        if mode == "current_backfill":
            warnings.append("Backtests apply today's fundamentals to past dates (look-ahead bias) until 1 year of weekly snapshots exists.")

    # --- scores & stance
    factors = scoring.factor_scores(w)
    for k, v in factors.items():
        w[f"score_{k}"] = v
    score = scoring.composite(factors, cfg.get("scoring", {}).get("weights", {}))
    score = score.where(w["bars"] >= min_bars)
    w["score"] = score.round(1)
    w["stance_code"] = scoring.stance(w, score, cfg)

    if start:
        s = pd.Timestamp(start) - pd.Timedelta(days=5)
        w = {k: v[v.index >= s] for k, v in w.items()}
        market = market[market.index >= s]
        sectors = {k: v[v.index >= s] for k, v in sectors.items()}
    return Analysis(w=w, market=market, sectors=sectors, universe=uni, sector_of=sector_of, name_of=name_of,
                    last_traded=last_traded, fundamentals_mode=mode, index_source=index_source, warnings=warnings)
