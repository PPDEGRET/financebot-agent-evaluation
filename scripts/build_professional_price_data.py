"""Download and filter broad professional-universe price data.

This script is intentionally separate from the active tournament. It creates new
cache files that can be supplied to run_hourly_agent_replay/run_agent_tournament
with --daily-cache/--hourly-cache/--universe-file.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from myaibot.core.showcase import require_external_runtime


def _load_symbols(path: Path) -> list[str]:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        if "symbol" not in df.columns:
            raise SystemExit(f"Universe CSV must have symbol column: {path}")
        raw = df["symbol"].tolist()
    else:
        raw = path.read_text(encoding="utf-8").splitlines()
    symbols = []
    seen = set()
    for item in raw:
        symbol = str(item).upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _extract_field(data: pd.DataFrame, field: str, symbols: list[str]) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        if field in data.columns.get_level_values(0):
            frame = data[field].copy()
        elif field in data.columns.get_level_values(1):
            frame = data.xs(field, axis=1, level=1).copy()
        else:
            return pd.DataFrame()
    else:
        if field in data.columns and len(symbols) == 1:
            frame = data[[field]].rename(columns={field: symbols[0]})
        elif field in data.columns:
            frame = data[[field]].copy()
        else:
            return pd.DataFrame()
    frame.columns = [str(c).upper().strip() for c in frame.columns]
    frame = frame.loc[:, ~pd.Index(frame.columns).duplicated()]
    return frame.apply(pd.to_numeric, errors="coerce")


def _download_batches(
    symbols: list[str],
    *,
    start: str,
    end: str,
    interval: str,
    batch_size: int,
    pause_seconds: float,
    auto_adjust: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Install yfinance with `pip install -e .[data]`.") from exc

    close_frames: list[pd.DataFrame] = []
    volume_frames: list[pd.DataFrame] = []
    failed_batches: list[dict[str, object]] = []
    total_batches = math.ceil(len(symbols) / batch_size) if symbols else 0
    for idx, batch in enumerate(_chunks(symbols, batch_size), start=1):
        print(f"download batch {idx}/{total_batches} interval={interval} symbols={len(batch)} first={batch[0]} last={batch[-1]}", flush=True)
        try:
            data = yf.download(
                tickers=" ".join(batch),
                start=start,
                end=end,
                interval=interval,
                auto_adjust=auto_adjust,
                group_by="column",
                progress=False,
                threads=True,
                prepost=False,
            )
        except Exception as exc:  # yfinance/network robustness
            failed_batches.append({"batch": idx, "symbols": batch, "error": repr(exc)[:1000]})
            continue
        close = _extract_field(data, "Close", batch)
        volume = _extract_field(data, "Volume", batch)
        if not close.empty:
            close_frames.append(close)
        if not volume.empty:
            volume_frames.append(volume)
        if pause_seconds:
            time.sleep(pause_seconds)
    close_all = pd.concat(close_frames, axis=1).sort_index() if close_frames else pd.DataFrame()
    volume_all = pd.concat(volume_frames, axis=1).sort_index() if volume_frames else pd.DataFrame()
    for frame in (close_all, volume_all):
        if not frame.empty:
            frame.columns = [str(c).upper().strip() for c in frame.columns]
            frame = frame.loc[:, ~pd.Index(frame.columns).duplicated()]
    meta = {"failed_batches": failed_batches, "requested_symbols": len(symbols), "interval": interval}
    return close_all, volume_all, meta


def _normalize_index(frame: pd.DataFrame, *, intraday: bool) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        if intraday:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        else:
            idx = idx.tz_convert(None)
    idx = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
    out.index = idx.floor("min") if intraday else idx.normalize()
    out = out[~out.index.duplicated(keep="last")]
    out.columns = [str(c).upper().strip() for c in out.columns]
    out = out.loc[:, ~pd.Index(out.columns).duplicated()]
    return out.apply(pd.to_numeric, errors="coerce").sort_index()


def _filter_liquid_symbols(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    *,
    min_price: float,
    min_adv_dollars: float,
    min_non_null_ratio: float,
    adv_window: int,
    liquidity_as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if close.empty:
        return pd.DataFrame(columns=["symbol", "last_price", "avg_dollar_volume", "non_null_ratio", "liquidity_as_of"])
    common = close.columns.intersection(volume.columns) if not volume.empty else close.columns
    close = close[common].sort_index()
    volume = volume.reindex(columns=common).sort_index() if not volume.empty else pd.DataFrame(index=close.index, columns=common)
    if liquidity_as_of is not None:
        cutoff = pd.Timestamp(liquidity_as_of).normalize()
        # If the selection date is the trading day under test, that day's close
        # and volume are not available before the market opens. Use strictly
        # prior daily rows for universe/liquidity eligibility.
        close_for_selection = close[close.index < cutoff]
        volume_for_selection = volume[volume.index < cutoff]
    else:
        cutoff = None
        close_for_selection = close
        volume_for_selection = volume
    if close_for_selection.empty:
        return pd.DataFrame(columns=["symbol", "last_price", "avg_dollar_volume", "non_null_ratio", "liquidity_as_of"])
    valid_ratio = close_for_selection.notna().mean()
    last = close_for_selection.ffill().iloc[-1]
    adv = (
        (close_for_selection * volume_for_selection)
        .rolling(adv_window, min_periods=max(5, adv_window // 3))
        .mean()
        .iloc[-1]
        if not volume_for_selection.empty
        else pd.Series(0.0, index=common)
    )
    rows = []
    for symbol in common:
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "last_price": float(last.get(symbol)) if pd.notna(last.get(symbol)) else None,
                "avg_dollar_volume": float(adv.get(symbol)) if pd.notna(adv.get(symbol)) else 0.0,
                "non_null_ratio": float(valid_ratio.get(symbol, 0.0)),
                "liquidity_as_of": None if cutoff is None else cutoff.date().isoformat(),
            }
        )
    stats = pd.DataFrame(rows)
    keep = (
        stats["last_price"].fillna(0) >= min_price
        ) & (stats["avg_dollar_volume"].fillna(0) >= min_adv_dollars) & (stats["non_null_ratio"] >= min_non_null_ratio)
    return stats.loc[keep].sort_values(["avg_dollar_volume", "last_price"], ascending=False).reset_index(drop=True)


def _clean_close(
    frame: pd.DataFrame,
    symbols: list[str],
    *,
    min_non_null_ratio: float | None = None,
    ratio_as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.reindex(columns=[s for s in symbols if s in frame.columns])
    if min_non_null_ratio is not None:
        ratio_frame = out
        if ratio_as_of is not None:
            ratio_frame = ratio_frame[ratio_frame.index < pd.Timestamp(ratio_as_of).normalize()]
        if not ratio_frame.empty:
            keep = ratio_frame.notna().mean() >= min_non_null_ratio
            out = out.reindex(columns=[str(c).upper() for c in keep[keep].index])
    return out.ffill(limit=5).sort_index()


def main() -> None:
    require_external_runtime("Network market-data download")
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/universes/professional_universe.csv")
    parser.add_argument("--out-dir", default="data/normalized/professional")
    parser.add_argument("--daily-start", default="2024-01-01")
    parser.add_argument("--daily-end", default="2026-06-20")
    parser.add_argument("--intraday-start", default="2026-01-02")
    parser.add_argument("--intraday-end", default="2026-06-20")
    parser.add_argument("--intraday-interval", choices=["30m", "60m", "1h"], default="60m")
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--pause-seconds", type=float, default=0.5)
    parser.add_argument("--max-symbols", type=int, default=None, help="Optional deterministic cap before downloads.")
    parser.add_argument("--intraday-max-symbols", type=int, default=None, help="Optional cap after liquidity ranking.")
    parser.add_argument("--min-price", type=float, default=3.0)
    parser.add_argument("--min-adv-dollars", type=float, default=5_000_000.0)
    parser.add_argument("--min-daily-ratio", type=float, default=0.80)
    parser.add_argument("--min-intraday-ratio", type=float, default=0.50)
    parser.add_argument("--adv-window", type=int, default=60)
    parser.add_argument(
        "--liquidity-as-of",
        default=None,
        help="Date used for liquidity/universe eligibility. Defaults to --intraday-start, using strictly prior daily rows.",
    )
    parser.add_argument(
        "--allow-future-coverage-filter",
        action="store_true",
        help="Apply non-null coverage filters over the full downloaded future period. Off by default because it is selection-biased.",
    )
    parser.add_argument("--skip-intraday", action="store_true")
    args = parser.parse_args()

    universe_path = ROOT / args.universe
    symbols = _load_symbols(universe_path)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    liquidity_as_of = args.liquidity_as_of or args.intraday_start

    daily_close_raw, daily_volume_raw, daily_meta = _download_batches(
        symbols,
        start=args.daily_start,
        end=args.daily_end,
        interval="1d",
        batch_size=args.batch_size,
        pause_seconds=args.pause_seconds,
    )
    daily_close_raw = _normalize_index(daily_close_raw, intraday=False)
    daily_volume_raw = _normalize_index(daily_volume_raw, intraday=False)
    liquid = _filter_liquid_symbols(
        daily_close_raw,
        daily_volume_raw,
        min_price=args.min_price,
        min_adv_dollars=args.min_adv_dollars,
        min_non_null_ratio=args.min_daily_ratio,
        adv_window=args.adv_window,
        liquidity_as_of=liquidity_as_of,
    )
    liquid_symbols = liquid["symbol"].tolist()
    if args.intraday_max_symbols:
        liquid_symbols = liquid_symbols[: args.intraday_max_symbols]
        liquid = liquid[liquid["symbol"].isin(liquid_symbols)].copy()

    daily_close = _clean_close(
        daily_close_raw,
        liquid_symbols,
        min_non_null_ratio=args.min_daily_ratio if args.allow_future_coverage_filter else None,
        ratio_as_of=None if args.allow_future_coverage_filter else liquidity_as_of,
    )
    daily_volume = _clean_close(
        daily_volume_raw,
        liquid_symbols,
        min_non_null_ratio=args.min_daily_ratio if args.allow_future_coverage_filter else None,
        ratio_as_of=None if args.allow_future_coverage_filter else liquidity_as_of,
    )

    daily_close_path = out_dir / "daily_close.csv"
    daily_volume_path = out_dir / "daily_volume.csv"
    liquid_path = out_dir / "liquid_universe.csv"
    symbols_path = out_dir / "liquid_symbols.txt"
    daily_close.to_csv(daily_close_path, index_label="date")
    daily_volume.to_csv(daily_volume_path, index_label="date")
    liquid.to_csv(liquid_path, index=False)
    symbols_path.write_text("\n".join(liquid_symbols) + "\n", encoding="utf-8")

    intraday_close_path = None
    intraday_meta: dict[str, object] = {"skipped": bool(args.skip_intraday)}
    intraday_symbols = liquid_symbols
    if not args.skip_intraday and liquid_symbols:
        intraday_close_raw, _, intraday_meta = _download_batches(
            liquid_symbols,
            start=args.intraday_start,
            end=args.intraday_end,
            interval=args.intraday_interval,
            batch_size=args.batch_size,
            pause_seconds=args.pause_seconds,
        )
        intraday_close = _normalize_index(intraday_close_raw, intraday=True)
        intraday_close = _clean_close(
            intraday_close,
            liquid_symbols,
            min_non_null_ratio=args.min_intraday_ratio if args.allow_future_coverage_filter else None,
        )
        intraday_symbols = list(intraday_close.columns)
        intraday_close_path = out_dir / f"intraday_close_{args.intraday_interval}.csv"
        intraday_close.to_csv(intraday_close_path, index_label="datetime")

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "universe": str(universe_path.relative_to(ROOT)),
        "requested_symbols": len(symbols),
        "liquid_symbols": len(liquid_symbols),
        "intraday_symbols": len(intraday_symbols),
        "daily_start": args.daily_start,
        "daily_end": args.daily_end,
        "intraday_start": args.intraday_start,
        "intraday_end": args.intraday_end,
        "intraday_interval": args.intraday_interval,
        "liquidity_as_of": liquidity_as_of,
        "liquidity_filter_note": "Liquidity stats use strictly prior daily rows before liquidity_as_of.",
        "allow_future_coverage_filter": bool(args.allow_future_coverage_filter),
        "filters": {
            "min_price": args.min_price,
            "min_adv_dollars": args.min_adv_dollars,
            "min_daily_ratio": args.min_daily_ratio,
            "min_intraday_ratio": args.min_intraday_ratio,
            "adv_window": args.adv_window,
            "intraday_max_symbols": args.intraday_max_symbols,
        },
        "outputs": {
            "daily_close": str(daily_close_path.relative_to(ROOT)),
            "daily_volume": str(daily_volume_path.relative_to(ROOT)),
            "liquid_universe": str(liquid_path.relative_to(ROOT)),
            "liquid_symbols": str(symbols_path.relative_to(ROOT)),
            "intraday_close": None if intraday_close_path is None else str(intraday_close_path.relative_to(ROOT)),
        },
        "daily_meta": daily_meta,
        "intraday_meta": intraday_meta,
        "bias_warning": "Uses current universe/listings and current Yahoo Finance downloadable history; not a paid point-in-time data product. Liquidity is as-of bounded unless allow_future_coverage_filter=true.",
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
