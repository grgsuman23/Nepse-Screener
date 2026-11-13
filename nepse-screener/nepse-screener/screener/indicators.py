"""Technical indicators (pure pandas/numpy — no TA-Lib needed).

All functions are vectorised over one stock's daily OHLCV frame."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(pd.Series(plus_dm, index=df.index), n) / tr
    minus_di = 100 * wilder(pd.Series(minus_dm, index=df.index), n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, n), plus_di, minus_di


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    fu, fl = upper.copy(), lower.copy()
    line = np.full(len(df), np.nan)
    direction = np.zeros(len(df))
    for i in range(len(df)):
        if np.isnan(a[i]):
            continue
        if i > 0 and not np.isnan(fu[i - 1]):
            fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
            prev_dir = direction[i - 1] if direction[i - 1] != 0 else 1
            if prev_dir > 0:
                direction[i] = -1 if close[i] < fl[i] else 1
            else:
                direction[i] = 1 if close[i] > fu[i] else -1
        else:
            direction[i] = 1
        line[i] = fl[i] if direction[i] > 0 else fu[i]
    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index)


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["volume"]
    pos = mf.where(tp > tp.shift(), 0.0).rolling(n).sum()
    neg = mf.where(tp < tp.shift(), 0.0).rolling(n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add the full indicator set to an OHLCV frame (date-indexed)."""
    d = df.copy()
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"].fillna(0)
    d["chg_pct"] = c.pct_change()
    for n in (20, 50, 200):
        d[f"sma_{n}"] = sma(c, n)
    for n in (9, 20, 50, 200):
        d[f"ema_{n}"] = ema(c, n)
    d["rsi_14"] = rsi(c, 14)
    d["rsi_2"] = rsi(c, 2)
    e12, e26 = ema(c, 12), ema(c, 26)
    d["macd"] = e12 - e26
    d["macd_signal"] = ema(d["macd"], 9)
    d["macd_hist"] = d["macd"] - d["macd_signal"]
    mid = sma(c, 20)
    sd = c.rolling(20).std()
    d["bb_mid"], d["bb_up"], d["bb_low"] = mid, mid + 2 * sd, mid - 2 * sd
    d["bb_pctb"] = (c - d["bb_low"]) / (d["bb_up"] - d["bb_low"])
    d["bb_width"] = (d["bb_up"] - d["bb_low"]) / mid
    d["atr_14"] = atr(d, 14)
    d["atr_pct"] = d["atr_14"] / c
    d["adx_14"], d["plus_di"], d["minus_di"] = adx(d, 14)
    d["st_line"], d["st_dir"] = supertrend(d, 10, 3.0)
    d["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    d["obv_ema_20"] = ema(d["obv"], 20)
    d["mfi_14"] = mfi(d, 14)
    ll14, hh14 = l.rolling(14).min(), h.rolling(14).max()
    d["stoch_k"] = 100 * (c - ll14) / (hh14 - ll14).replace(0, np.nan)
    d["stoch_d"] = d["stoch_k"].rolling(3).mean()
    d["vol_sma_20"] = v.rolling(20, min_periods=10).mean()
    d["vol_ratio"] = v / d["vol_sma_20"].replace(0, np.nan)
    to = d["turnover"].fillna(c * v)
    d["turnover_sma_20"] = to.rolling(20, min_periods=10).mean()
    # breakout levels use the PREVIOUS n days (exclude today)
    for n in (20, 55, 250):
        d[f"hh_{n}"] = h.rolling(n, min_periods=min(n, 20)).max().shift()
        d[f"ll_{n}"] = l.rolling(n, min_periods=min(n, 20)).min().shift()
    hi52 = h.rolling(250, min_periods=60).max()
    lo52 = l.rolling(250, min_periods=60).min()
    d["high_52w_t"], d["low_52w_t"] = hi52, lo52
    d["pct_from_52w_high"] = c / hi52 - 1
    d["pct_from_52w_low"] = c / lo52 - 1
    for name, n in (("ret_1w", 5), ("ret_1m", 21), ("ret_3m", 63), ("ret_6m", 126), ("ret_12m", 250)):
        d[name] = c / c.shift(n) - 1
    d["volatility_20"] = d["chg_pct"].rolling(20).std() * np.sqrt(230)
    d["dist_sma200"] = c / d["sma_200"] - 1
    d["dist_ema50"] = c / d["ema_50"] - 1
    d["sma200_slope"] = d["sma_200"] / d["sma_200"].shift(20) - 1
    d["ema50_slope"] = d["ema_50"] / d["ema_50"].shift(10) - 1
    d["bars"] = np.arange(1, len(d) + 1)
    return d


INDICATOR_HELP = {
    "close": "Last traded / closing price (NPR, adjusted for bonus & right shares)",
    "chg_pct": "1-day % change (0.05 = +5%)",
    "sma_20/50/200": "Simple moving averages",
    "ema_9/20/50/200": "Exponential moving averages",
    "rsi_14, rsi_2": "Relative Strength Index (0-100)",
    "macd, macd_signal, macd_hist": "MACD (12,26,9)",
    "bb_up/bb_mid/bb_low, bb_pctb, bb_width": "Bollinger Bands (20, 2σ)",
    "atr_14, atr_pct": "Average True Range (absolute / as % of price)",
    "adx_14, plus_di, minus_di": "Trend strength (ADX) and direction (DI)",
    "st_line, st_dir": "Supertrend (10, 3); st_dir 1 = bullish, -1 = bearish",
    "obv, obv_ema_20": "On-Balance Volume",
    "mfi_14": "Money Flow Index",
    "stoch_k, stoch_d": "Stochastic oscillator",
    "vol_ratio": "Today's volume ÷ 20-day average volume",
    "turnover_sma_20": "20-day average turnover (NPR)",
    "hh_20/55/250, ll_20/55/250": "Highest high / lowest low of the PREVIOUS n days",
    "pct_from_52w_high/low": "Distance from 52-week high / low",
    "ret_1w/1m/3m/6m/12m": "Price return over the period",
    "volatility_20": "Annualised 20-day volatility",
    "dist_sma200, dist_ema50": "% distance from the average",
    "sma200_slope, ema50_slope": "Slope of the average (rising > 0)",
    "rs_rating": "Relative-strength rating 1-99 vs all stocks (IBD style)",
    "sector_rs_rank": "Sector's relative-strength rank 0-100",
    "regime": "Market regime: 1 bull, 0 neutral, -1 bear",
    "breadth_50, breadth_200": "Share of stocks above their 50/200-day averages",
    "liquid": "1 if 20-day avg turnover ≥ the liquidity threshold",
    "eps, bvps, pe, pbv, roe": "Fundamentals (P/E and P/B use the latest price)",
    "div_yield, earnings_yield": "Cash-dividend yield, earnings yield",
    "graham, graham_discount": "Graham number and its premium over price",
    "pe_vs_sector, pbv_vs_sector": "Ratio ÷ sector median (below 1 = cheaper than peers)",
    "score_* , score": "Factor scores and composite score (0-100)",
    "stance_code": "2 Strong Buy, 1 Buy, 0 Hold, -1 Reduce, -2 Exit",
}
