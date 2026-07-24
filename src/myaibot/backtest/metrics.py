"""Backtest metrics for promotion decisions."""

from __future__ import annotations

import math

import pandas as pd


def equity_curve_from_snapshots(snapshots: list[dict]) -> pd.Series:
    if not snapshots:
        return pd.Series(dtype=float)
    df = pd.DataFrame(snapshots)
    series = pd.Series(pd.to_numeric(df["equity"], errors="coerce").values, index=pd.to_datetime(df["as_of"]))
    return series.dropna()


def performance_summary(equity: pd.Series, *, initial_cash: float) -> dict[str, float]:
    if equity.empty:
        return {"total_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "final_equity": initial_cash}
    returns = equity.pct_change(fill_method=None).dropna()
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    if returns.std(ddof=0) > 0:
        sharpe = float(math.sqrt(252) * returns.mean() / returns.std(ddof=0))
    else:
        sharpe = 0.0
    return {"total_return": total_return, "max_drawdown": max_drawdown, "sharpe": sharpe, "final_equity": float(equity.iloc[-1])}


def benchmark_return(price_frame: pd.DataFrame, symbol: str, start: str, end: str) -> float | None:
    if symbol not in price_frame.columns:
        return None
    series = price_frame.loc[pd.Timestamp(start) : pd.Timestamp(end), symbol].dropna()
    if len(series) < 2 or float(series.iloc[0]) <= 0:
        return None
    return float(series.iloc[-1] / series.iloc[0] - 1.0)
