"""Layout-tolerant HTML helpers.

Financial portals change their CSS often but keep their *labels* ("EPS",
"P/E Ratio", "Book Value"...). We therefore harvest every label/value pair on
the page (table rows, definition lists, label/value div pairs) and map labels
with regular expressions, instead of relying on fragile CSS selectors."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..utils import to_num

LABELS = {
    "sector": r"^sector$",
    "shares_outstanding": r"shares?\s*outstanding|total\s*listed\s*shares?|listed\s*shares?",
    "ltp": r"^(market\s*price|ltp|last\s*traded\s*price|close\s*price)$",
    "eps": r"^eps\b|earnings?\s*per\s*share",
    "pe": r"^p\s*/\s*e(\s*ratio)?$|^pe\s*ratio$",
    "bvps": r"^book\s*value|net\s*worth\s*per\s*share|^bvps",
    "pbv": r"^pbv$|^p\s*/\s*b(v)?(\s*ratio)?$|price\s*to\s*book",
    "div_pct": r"^%?\s*dividend|cash\s*dividend",
    "bonus_pct": r"^%?\s*bonus|bonus\s*share",
    "mcap": r"market\s*capitali[sz]ation",
    "yield_1y": r"1\s*year\s*yield",
    "avg_vol_30": r"30.?day\s*avg",
    "range_52w": r"52\s*weeks?\s*high",
    "roe": r"^roe\b|return\s*on\s*equity",
    "npl": r"^npl\b|non.?performing",
    "car": r"^car\b|capital\s*adequacy",
}
_compiled = {k: re.compile(v, re.I) for k, v in LABELS.items()}
_fy_re = re.compile(r"FY\s*:?\s*([0-9]{2,4}\s*[-/]\s*[0-9]{2,4})(?:\s*,?\s*Q\s*:?\s*([1-4]))?", re.I)


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("\xa0", " ")).strip()


def label_value_pairs(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    pairs: list[tuple[str, str]] = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            pairs.append((_clean(cells[0].get_text(" ")), _clean(cells[1].get_text(" "))))
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            pairs.append((_clean(dt.get_text(" ")), _clean(dd.get_text(" "))))
    # label/value sibling divs or spans (common in modern layouts)
    for el in soup.find_all(["div", "span", "p", "label", "strong", "b"]):
        if el.find(True) is not None:
            continue
        lab = _clean(el.get_text(" "))
        if not lab or len(lab) > 40:
            continue
        sib = el.find_next_sibling()
        if sib is not None:
            val = _clean(sib.get_text(" "))
            if val and len(val) < 80:
                pairs.append((lab, val))
    return pairs


def extract_fundamentals(html: str) -> dict:
    out: dict = {}
    for label, value in label_value_pairs(html):
        lab = label.strip(" :")
        for key, rx in _compiled.items():
            if key in out:
                continue
            if rx.search(lab):
                if key == "sector":
                    if value and not re.search(r"\d{3,}", value):
                        out[key] = value
                elif key == "range_52w":
                    nums = re.findall(r"[\d,]+\.?\d*", value)
                    if len(nums) >= 2:
                        out["high_52w"], out["low_52w"] = to_num(nums[0]), to_num(nums[1])
                        out[key] = value
                else:
                    v = to_num(value)
                    if v == v:  # not NaN
                        out[key] = v
                        if key == "eps":
                            m = _fy_re.search(value)
                            if m:
                                out["eps_period"] = f"FY {m.group(1).replace(' ', '')}" + (f" Q{m.group(2)}" if m.group(2) else "")
                break
    return out


def parse_table(html: str, must_have: str = "symbol") -> list[dict]:
    """Return rows (as dicts keyed by lower-cased header) of the first table
    whose header contains `must_have`."""
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        head = table.find("thead") or table
        ths = head.find_all("th")
        headers = [_clean(th.get_text(" ")).lower() for th in ths]
        if not any(must_have in h for h in headers):
            continue
        rows = []
        body = table.find("tbody") or table
        for tr in body.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            vals = [_clean(td.get_text(" ")) for td in tds]
            hs = headers[-len(vals):] if len(headers) >= len(vals) else headers + [f"c{i}" for i in range(len(vals) - len(headers))]
            rows.append(dict(zip(hs, vals)))
        if rows:
            return rows
    return []
