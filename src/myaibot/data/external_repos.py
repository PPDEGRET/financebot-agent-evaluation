"""Portable adapters for explicitly supplied research-export paths.

The portfolio package contains no private sibling-repository paths or data. A
caller must provide a path to a safe, authorized export explicitly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

def month_range(start: str | pd.Timestamp, end: str | pd.Timestamp) -> list[pd.Period]:
    start_period = pd.Period(pd.Timestamp(start), freq="M")
    end_period = pd.Period(pd.Timestamp(end), freq="M")
    return list(pd.period_range(start_period, end_period, freq="M"))


def load_wsb_daily_counts(
    repo_path: str | Path,
    *,
    start: str | pd.Timestamp = "2021-01-01",
    end: str | pd.Timestamp = "2026-06-17",
) -> pd.DataFrame:
    """Load WSB daily ticker counts from the existing repo's monthly Parquet outputs."""
    root = Path(repo_path)
    out_dir = root / "outputs" / "wsb_mentions"
    frames: list[pd.DataFrame] = []
    for period in month_range(start, end):
        path = out_dir / f"wsb_daily_ticker_counts_{period.year:04d}-{period.month:02d}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=["date", "ticker", "mention_count"])
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["mention_count"] = pd.to_numeric(df["mention_count"], errors="coerce").fillna(0).astype(int)
    mask = (df["date"] >= pd.Timestamp(start).normalize()) & (df["date"] <= pd.Timestamp(end).normalize())
    return df.loc[mask, ["date", "ticker", "mention_count"]].sort_values(["date", "ticker"]).reset_index(drop=True)


def load_glassdoor_signal_panel(
    repo_path: str | Path,
    *,
    features_only: bool = True,
) -> pd.DataFrame:
    """Load the Glassdoor panel and optionally strip future labels.

    `forward_return` and `label_up` are labels, not live features. Keep them only
    inside properly purged training code.
    """
    path = Path(repo_path) / "data" / "processed" / "signal_panel.csv"
    df = pd.read_csv(path, parse_dates=["as_of"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    if features_only:
        return df.drop(columns=[col for col in ["forward_return", "label_up"] if col in df.columns])
    return df


def load_yahoo_chart_cache_file(path: str | Path) -> pd.Series:
    """Parse one Yahoo chart JSON cache file from the WSB repo into adjusted closes."""
    cache_path = Path(path)
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    chart = raw.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error in {cache_path}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No chart result in {cache_path}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjclose = (indicators.get("adjclose") or [{}])[0].get("adjclose")
    close = adjclose or quote.get("close")
    if not timestamps or not close:
        raise RuntimeError(f"No daily closes in {cache_path}")
    symbol = cache_path.name.split("_")[0].upper()
    series = pd.Series(close, index=pd.to_datetime(timestamps, unit="s").normalize(), name=symbol)
    return pd.to_numeric(series, errors="coerce").dropna().sort_index()


def load_wsb_cached_price_frame(
    repo_path: str | Path,
    *,
    symbols: list[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    """Load a price frame from the WSB repo's Yahoo chart cache when present.

    Chooses the widest cached file per symbol whose name matches the WSB cache
    convention. Missing symbols are skipped.
    """
    cache_dir = Path(repo_path) / "data" / "cache" / "prices"
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    series_list: list[pd.Series] = []
    for symbol in [s.upper().strip() for s in symbols]:
        candidates = sorted(cache_dir.glob(f"{symbol}_yahoo_chart_*.json"), key=_cache_span_key, reverse=True)
        for candidate in candidates:
            try:
                s = load_yahoo_chart_cache_file(candidate)
            except Exception:
                continue
            clipped = s[(s.index >= start_ts) & (s.index <= end_ts)]
            if not clipped.empty:
                series_list.append(clipped.rename(symbol))
                break
    if not series_list:
        return pd.DataFrame()
    return pd.concat(series_list, axis=1).sort_index()


def _cache_span_key(path: Path) -> tuple[int, int]:
    match = re.search(r"_(\d{8})_(\d{8})\.json$", path.name)
    if not match:
        return (0, 0)
    start, end = match.groups()
    return (int(end) - int(start), int(end))
