"""Historical price ingestion and hygiene."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class PriceDownloadConfig:
    start: str
    end: str
    interval: str = "1d"
    auto_adjust: bool = True
    min_non_null_ratio: float = 0.80


def download_yfinance_price_frame(symbols: list[str], config: PriceDownloadConfig, out_path: str | Path | None = None) -> pd.DataFrame:
    """Download research-grade adjusted close prices from yfinance."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install optional dependency with `pip install -e .[data]` for yfinance prices.") from exc
    data = yf.download(
        tickers=" ".join(sorted(set(s.upper() for s in symbols))),
        start=config.start,
        end=config.end,
        interval=config.interval,
        auto_adjust=config.auto_adjust,
        group_by="column",
        progress=False,
        threads=True,
    )
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        field = "Close" if "Close" in data.columns.get_level_values(0) else "Adj Close"
        closes = data[field].copy()
    else:
        closes = data[["Close"]].rename(columns={"Close": symbols[0].upper()}) if "Close" in data.columns else data
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    closes.columns = [str(c).upper() for c in closes.columns]
    closes = clean_price_frame(closes, min_non_null_ratio=config.min_non_null_ratio)
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        closes.to_csv(path, index_label="date")
    return closes


def clean_price_frame(frame: pd.DataFrame, *, min_non_null_ratio: float = 0.80) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    df = frame.copy().sort_index()
    df.columns = [str(c).upper().strip() for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(axis=1, thresh=max(1, int(len(df) * min_non_null_ratio)))
    df = df.ffill(limit=5)
    return df


def compute_adv_dollars(close: pd.DataFrame, volume: pd.DataFrame | None = None, window: int = 60) -> dict[str, float]:
    if close.empty or volume is None or volume.empty:
        return {}
    close = close[close.columns.intersection(volume.columns)]
    volume = volume[close.columns]
    adv = (close * volume).rolling(window).mean().iloc[-1].dropna()
    return {str(k).upper(): float(v) for k, v in adv.items()}


def load_price_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return clean_price_frame(df, min_non_null_ratio=0.0)


def price_frame_summary(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {"rows": 0, "symbols": 0}
    return {
        "rows": len(frame),
        "symbols": len(frame.columns),
        "start": str(frame.index.min().date()),
        "end": str(frame.index.max().date()),
        "columns": list(frame.columns),
    }
