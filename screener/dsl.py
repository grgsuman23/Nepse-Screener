"""A small, safe expression language for screens and strategies.

    close > sma_200 and cross_above(ema_20, ema_50) and rsi_14 < 70

Expressions are parsed with Python's `ast` module and only a whitelisted set
of operations is allowed (no attribute access, imports or calls to arbitrary
code). Names resolve to wide DataFrames (dates × symbols) so each expression
is evaluated for all stocks and all dates at once."""
from __future__ import annotations

import ast
import operator

import numpy as np
import pandas as pd


class DSLError(ValueError):
    pass


def _df(x, like: pd.DataFrame):
    if isinstance(x, pd.DataFrame):
        return x
    return pd.DataFrame(x, index=like.index, columns=like.columns)


def _bool(x) -> pd.DataFrame:
    if isinstance(x, pd.DataFrame):
        return x.fillna(False).astype(bool) if x.dtypes.apply(lambda t: t == bool).all() else (x.fillna(0) != 0)
    return x


def _cross_above(a, b):
    return (a > b) & (a.shift(1) <= (b.shift(1) if isinstance(b, pd.DataFrame) else b))


def _cross_below(a, b):
    return (a < b) & (a.shift(1) >= (b.shift(1) if isinstance(b, pd.DataFrame) else b))


def _bars_since(cond):
    c = _bool(cond).astype(int)
    out = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    arr = c.to_numpy()
    res = np.full(arr.shape, np.nan)
    last = np.full(arr.shape[1], -1)
    for i in range(arr.shape[0]):
        hit = arr[i] == 1
        last[hit] = i
        valid = last >= 0
        res[i, valid] = i - last[valid]
    out[:] = res
    return out


FUNCS = {
    "cross_above": _cross_above,
    "cross_below": _cross_below,
    "shift": lambda a, n=1: a.shift(int(n)),
    "ref": lambda a, n=1: a.shift(int(n)),
    "highest": lambda a, n: a.rolling(int(n), min_periods=1).max(),
    "lowest": lambda a, n: a.rolling(int(n), min_periods=1).min(),
    "sma": lambda a, n: a.rolling(int(n), min_periods=int(n)).mean(),
    "ema": lambda a, n: a.ewm(span=int(n), adjust=False, min_periods=int(n)).mean(),
    "rising": lambda a, n=1: a > a.shift(int(n)),
    "falling": lambda a, n=1: a < a.shift(int(n)),
    "change": lambda a, n=1: a / a.shift(int(n)) - 1,
    "bars_since": _bars_since,
    "abs": lambda a: a.abs() if hasattr(a, "abs") else abs(a),
    "min": lambda a, b: np.minimum(a, b),
    "max": lambda a, b: np.maximum(a, b),
}

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod}
_CMP = {ast.Gt: operator.gt, ast.Lt: operator.lt, ast.GtE: operator.ge, ast.LtE: operator.le,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}


class Evaluator:
    def __init__(self, fields: dict[str, pd.DataFrame]):
        self.fields = fields
        self.like = fields["close"]

    def names(self, expr: str) -> set[str]:
        tree = ast.parse(_normalise(expr), mode="eval")
        return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in FUNCS and n.id not in ("True", "False")}

    def validate(self, expr: str) -> list[str]:
        missing = sorted(n for n in self.names(expr) if n not in self.fields)
        return missing

    def eval(self, expr: str):
        expr = _normalise(expr)
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise DSLError(f"Syntax error in expression: {expr!r} ({e.msg})") from None
        return self._ev(tree.body)

    def mask(self, expr: str) -> pd.DataFrame:
        r = self.eval(expr)
        if not isinstance(r, pd.DataFrame):
            r = _df(np.full(self.like.shape, bool(r)), self.like)
        return _bool(r)

    def _ev(self, n):
        if isinstance(n, ast.BoolOp):
            vals = [_bool(self._ev(v)) for v in n.values]
            out = vals[0]
            for v in vals[1:]:
                out = (out & v) if isinstance(n.op, ast.And) else (out | v)
            return out
        if isinstance(n, ast.UnaryOp):
            v = self._ev(n.operand)
            if isinstance(n.op, ast.Not):
                return ~_bool(v)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return v
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](self._ev(n.left), self._ev(n.right))
        if isinstance(n, ast.Compare):
            left = self._ev(n.left)
            out = None
            for op, comp in zip(n.ops, n.comparators):
                if type(op) not in _CMP:
                    raise DSLError("Unsupported comparison")
                right = self._ev(comp)
                r = _CMP[type(op)](left, right)
                out = r if out is None else (out & r)
                left = right
            return out
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in FUNCS:
                raise DSLError(f"Unknown function: {ast.unparse(n.func)}")
            args = [self._ev(a) for a in n.args]
            return FUNCS[n.func.id](*args)
        if isinstance(n, ast.Name):
            if n.id in ("True", "true"):
                return True
            if n.id in ("False", "false"):
                return False
            if n.id not in self.fields:
                raise DSLError(f"Unknown field '{n.id}'. See README field reference.")
            return self.fields[n.id]
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float, bool)):
            return n.value
        raise DSLError(f"Unsupported syntax: {ast.unparse(n)}")


def _normalise(expr: str) -> str:
    e = " ".join(str(expr).split())
    return e.replace("&&", " and ").replace("||", " or ")
