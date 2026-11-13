"""Data-source adapters. Each returns normalised pandas objects:

prices  : DataFrame[date, open, high, low, close, volume, turnover, prev_close?]
universe: DataFrame[symbol, name, sector, security_id]
fundamentals: dict per symbol
"""

OHLC_COLS = ["date", "open", "high", "low", "close", "volume", "turnover"]
