"""Corporate actions snapshots.

For now yfinance is a research-grade proxy. The interface records observation
time so a paid point-in-time corporate-actions provider can replace it later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def fetch_yfinance_actions(symbols: list[str], out_path: str | Path | None = None) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install optional dependency with `pip install -e .[data]` for yfinance actions.") from exc
    observed_at = datetime.now(UTC)
    rows = []
    for symbol in symbols:
        ticker = yf.Ticker(symbol)
        try:
            actions = ticker.actions.reset_index()
        except Exception:
            continue
        if actions.empty:
            continue
        actions["symbol"] = symbol.upper()
        actions["observed_at_utc"] = observed_at
        rows.append(actions)
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df


def flag_large_adjustment_jumps(price_frame: pd.DataFrame, threshold: float = 0.5) -> pd.DataFrame:
    """Flag very large one-day adjusted-close moves for manual action validation."""
    returns = price_frame.sort_index().pct_change(fill_method=None)
    rows = []
    for date, row in returns.iterrows():
        for symbol, value in row.dropna().items():
            if abs(float(value)) >= threshold:
                rows.append({"date": date, "symbol": symbol, "return": float(value), "reason": "large_adjusted_move"})
    return pd.DataFrame(rows)
