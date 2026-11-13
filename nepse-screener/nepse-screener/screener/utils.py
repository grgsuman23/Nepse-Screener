"""Small shared helpers: logging, HTTP with retries, number parsing."""
from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("screener")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class Http:
    """requests.Session with retries, polite delay and timeouts."""

    def __init__(self, delay: float = 0.7, timeout: float = 25, retries: int = 3):
        import requests

        self.s = requests.Session()
        self.s.headers.update(BROWSER_HEADERS)
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0

    def get(self, url: str, **kw):
        last_err = None
        for attempt in range(self.retries):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last = time.time()
                r = self.s.get(url, timeout=self.timeout, **kw)
                if r.status_code == 200:
                    return r
                last_err = RuntimeError(f"HTTP {r.status_code} for {url}")
            except Exception as e:  # network errors
                last_err = e
            time.sleep(1.5 * (attempt + 1))
        raise last_err  # type: ignore[misc]


_num_re = re.compile(r"-?\(?[\d,]*\.?\d+\)?")


def to_num(text: Any) -> float:
    """Parse '1,23,456.78', '(12.5)', '24.50 (FY:081-082, Q:4)', '12.5%' -> float."""
    if text is None:
        return float("nan")
    if isinstance(text, (int, float, np.number)):
        return float(text)
    t = str(text).strip().replace("\xa0", " ")
    if t in ("", "-", "--", "N/A", "NA", "null", "None"):
        return float("nan")
    m = _num_re.search(t)
    if not m:
        return float("nan")
    s = m.group(0)
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()-").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return float("nan")
    return -v if neg else v


def safe(v: Any, nd: int = 4):
    """Make a value JSON-safe (NaN/inf -> None, numpy -> python)."""
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(float(v), nd)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime("%Y-%m-%d")
    return v


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def default(o):
        r = safe(o)
        if r is o:
            return str(o)
        return r

    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        return safe(o)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean(obj), fh, separators=(",", ":"), default=default, allow_nan=False)


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default
