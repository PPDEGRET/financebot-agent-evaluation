"""Hourly market-clock and price helpers for agentic replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from myaibot.core.showcase import require_external_runtime


MARKET_HOURS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]


def market_grid_times(interval_minutes: int = 60) -> list[str]:
    """Return regular US market timestamps from 09:30 through 15:30 inclusive."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    start = pd.Timestamp("2000-01-01 09:30")
    end = pd.Timestamp("2000-01-01 15:30")
    times = []
    cur = start
    while cur <= end:
        times.append(cur.strftime("%H:%M"))
        cur += pd.Timedelta(minutes=interval_minutes)
    if times[-1] != "15:30":
        times.append("15:30")
    return times


@dataclass(frozen=True)
class HourlyDownloadConfig:
    start: str
    end: str
    interval: str = "60m"
    auto_adjust: bool = True


def market_hour_index(daily_index: pd.DatetimeIndex, *, hours: list[str] | None = None) -> pd.DatetimeIndex:
    """Create naive US-market hourly timestamps for each daily trading date."""
    hours = hours or MARKET_HOURS
    stamps = []
    for day in pd.DatetimeIndex(daily_index).normalize().unique():
        for hour in hours:
            stamps.append(pd.Timestamp(f"{day.date()} {hour}"))
    return pd.DatetimeIndex(stamps)


def download_yfinance_hourly_frame(symbols: list[str], config: HourlyDownloadConfig, out_path: str | Path | None = None) -> pd.DataFrame:
    require_external_runtime("Network market-data download")
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install yfinance to download hourly prices.") from exc
    data = yf.download(
        tickers=" ".join(sorted(set(s.upper() for s in symbols))),
        start=config.start,
        end=config.end,
        interval=config.interval,
        auto_adjust=config.auto_adjust,
        group_by="column",
        progress=False,
        threads=True,
        prepost=False,
    )
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        field = "Close" if "Close" in data.columns.get_level_values(0) else "Adj Close"
        closes = data[field].copy()
    else:
        closes = data[["Close"]].rename(columns={"Close": symbols[0].upper()}) if "Close" in data.columns else data
    idx = pd.to_datetime(closes.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    closes.index = idx.floor("min")
    closes.columns = [str(c).upper() for c in closes.columns]
    closes = closes.apply(pd.to_numeric, errors="coerce").sort_index().dropna(axis=1, how="all")
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        closes.to_csv(path, index_label="datetime")
    return closes


def load_hourly_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    df.index = pd.to_datetime(df.index).tz_localize(None).floor("min")
    df.columns = [str(c).upper() for c in df.columns]
    return df.apply(pd.to_numeric, errors="coerce").sort_index()


def daily_to_hourly_previous_close(daily: pd.DataFrame, *, hours: list[str] | None = None) -> pd.DataFrame:
    """Fallback hourly frame that exposes only previous close intraday and current close at 15:30.

    This is timestamp-conservative when real hourly data is unavailable: before
    the close, the simulated intraday price is the previous known close.
    """
    daily = daily.sort_index().copy()
    daily.index = pd.to_datetime(daily.index).normalize()
    hours = hours or MARKET_HOURS
    rows = []
    idx = []
    prev = None
    for day, row in daily.iterrows():
        for hour in hours:
            idx.append(pd.Timestamp(f"{day.date()} {hour}"))
            if prev is None or hour == hours[-1]:
                rows.append(row)
            else:
                rows.append(prev)
        prev = row
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=daily.columns)


def align_hourly_to_market_hours(hourly: pd.DataFrame, daily_fallback: pd.DataFrame | None = None, *, hours: list[str] | None = None) -> pd.DataFrame:
    """Normalize downloaded intraday bars to the project's market-hour grid."""
    if hourly.empty:
        return hourly
    df = hourly.copy().sort_index()
    df.index = pd.to_datetime(df.index).tz_localize(None).floor("30min")
    df = df[~df.index.duplicated(keep="last")]
    if daily_fallback is None:
        return df
    target = market_hour_index(daily_fallback.index, hours=hours)
    target = target[(target >= df.index.min()) & (target <= df.index.max())]
    return df.reindex(target).ffill().combine_first(daily_to_hourly_previous_close(daily_fallback, hours=hours).reindex(target)).dropna(how="all")
