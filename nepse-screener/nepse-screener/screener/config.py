"""Settings, strategy definitions and file-system layout."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def prices(self) -> Path:
        return self.data / "prices"

    @property
    def index(self) -> Path:
        return self.data / "index"

    @property
    def fundamentals(self) -> Path:
        return self.data / "fundamentals"

    @property
    def snapshots(self) -> Path:
        return self.fundamentals / "snapshots"

    @property
    def tracking(self) -> Path:
        return self.data / "tracking"

    @property
    def universe(self) -> Path:
        return self.data / "universe.csv"

    @property
    def corporate_actions(self) -> Path:
        return self.data / "corporate_actions.csv"

    @property
    def status(self) -> Path:
        return self.data / "source_status.json"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def out(self) -> Path:
        return self.docs / "data"

    def ensure(self) -> "Paths":
        for p in (self.prices, self.index, self.snapshots, self.tracking, self.out, self.out / "history"):
            p.mkdir(parents=True, exist_ok=True)
        return self


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_dir: Path | None = None) -> dict:
    config_dir = config_dir or ROOT / "config"
    return load_yaml(config_dir / "settings.yaml")


def load_strategies(config_dir: Path | None = None) -> list[dict]:
    config_dir = config_dir or ROOT / "config"
    return load_yaml(config_dir / "strategies.yaml").get("strategies", [])


def get_paths(root: str | Path | None = None) -> Paths:
    root = Path(root or os.environ.get("SCREENER_ROOT") or ROOT)
    return Paths(root).ensure()


def materialize_strategy(strategy: dict, params: dict | None = None) -> dict:
    """Substitute {param} placeholders with concrete values.

    Uses the strategy's `defaults` unless explicit params are given."""
    values = dict(strategy.get("defaults") or {})
    if params:
        values.update(params)
    s = copy.deepcopy(strategy)

    def sub(v):
        if isinstance(v, str) and "{" in v:
            out = v
            for k, val in values.items():
                out = out.replace("{" + k + "}", str(val))
            try:  # numeric fields such as stop_atr / top_n
                f = float(out)
                return int(f) if f.is_integer() and k_is_int(out) else f
            except ValueError:
                return out
        return v

    for key, val in list(s.items()):
        if key in ("params", "defaults", "description", "name", "id"):
            continue
        s[key] = sub(val)
    s["_params"] = values
    return s


def k_is_int(text: str) -> bool:
    return "." not in text and "e" not in text.lower()
