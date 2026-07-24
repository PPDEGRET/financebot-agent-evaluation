#!/usr/bin/env python3
"""Generate the repository's deterministic, non-market sample dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "synthetic"
SYMBOLS = ["SYN_BENCH", "SYN_ORBIT", "SYN_CEDAR", "SYN_HARBOR", "SYN_RIDGE"]
HOURS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    days = pd.bdate_range("2025-10-13", periods=90)
    t = np.arange(len(days), dtype=float)

    daily = pd.DataFrame(
        {
            "SYN_BENCH": 100.0 * np.exp(0.0007 * t + 0.012 * np.sin(t / 8.0)),
            "SYN_ORBIT": 48.0 * np.exp(0.0019 * t + 0.030 * np.sin(t / 6.0)),
            "SYN_CEDAR": 72.0 * np.exp(0.0012 * t + 0.022 * np.cos(t / 7.0)),
            "SYN_HARBOR": 36.0 * np.exp(0.0003 * t - 0.026 * np.sin(t / 5.0)),
            "SYN_RIDGE": 58.0 * np.exp(-0.0002 * t + 0.035 * np.sin(t / 9.0 + 1.2)),
        },
        index=days,
    )
    daily.index.name = "date"

    volume = pd.DataFrame(index=days)
    for idx, symbol in enumerate(SYMBOLS):
        volume[symbol] = 1_200_000 + idx * 175_000 + (t % 11) * 21_000 + 35_000 * np.sin(t / (4.0 + idx))
    volume.index.name = "date"

    intraday_offsets = np.array([-0.0040, -0.0020, 0.0010, -0.0010, 0.0025, 0.0015, 0.0])
    rows: list[dict[str, float | pd.Timestamp]] = []
    for day_index, day in enumerate(days):
        for hour_index, hour in enumerate(HOURS):
            row: dict[str, float | pd.Timestamp] = {"timestamp": pd.Timestamp(f"{day.date()} {hour}")}
            for symbol_index, symbol in enumerate(SYMBOLS):
                phase = math.sin(day_index / (5.0 + symbol_index) + symbol_index)
                signed_offset = intraday_offsets[hour_index] * (1.0 + 0.15 * symbol_index) + phase * 0.0007
                if hour_index == len(HOURS) - 1:
                    signed_offset = 0.0
                row[symbol] = float(daily.iloc[day_index][symbol] * (1.0 + signed_offset))
            rows.append(row)
    hourly = pd.DataFrame(rows).set_index("timestamp")
    hourly.index.name = "timestamp"
    return daily, volume, hourly


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(float_format="%.6f", lineterminator="\n").encode("utf-8")


def _payloads() -> dict[str, bytes]:
    daily, volume, hourly = _frames()
    payloads = {
        "daily_prices.csv": _csv_bytes(daily),
        "daily_volume.csv": _csv_bytes(volume),
        "hourly_prices.csv": _csv_bytes(hourly),
    }
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
    manifest = {
        "schema_version": 1,
        "label": "Synthetic demonstration",
        "origin": "Generated locally by scripts/generate_sample_data.py; contains no observed market data.",
        "rights": "Original generated fixture; no external dataset license applies. Repository license intentionally not selected.",
        "generator_version": "1.0.0",
        "generated_at": "2026-07-14T00:00:00Z",
        "date_range": {"start": "2025-10-13", "end": "2026-02-13"},
        "symbols": SYMBOLS,
        "review_times": HOURS,
        "files": {name: {"sha256": digest} for name, digest in hashes.items()},
        "limitations": [
            "Prices are smooth mathematical series designed to exercise code paths, not resemble a validated market process.",
            "Results on this fixture have no financial meaning and cannot reproduce the historical tournament.",
        ],
    }
    payloads["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return payloads


def write_dataset(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in _payloads().items():
        (output / name).write_bytes(payload)


def check_dataset(output: Path) -> list[str]:
    problems: list[str] = []
    expected = _payloads()
    for name, payload in expected.items():
        path = output / name
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        elif path.read_bytes() != payload:
            problems.append(f"content mismatch: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    extra = sorted(path.name for path in output.glob("*") if path.is_file() and path.name not in expected)
    problems.extend(f"unexpected file: {name}" for name in extra)
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Verify committed files match the deterministic generator.")
    args = parser.parse_args()

    if args.check:
        problems = check_dataset(args.output)
        if problems:
            raise SystemExit("Synthetic dataset check failed:\n- " + "\n- ".join(problems))
        print("Synthetic dataset check passed: 3 CSV files + manifest are deterministic.")
        return

    write_dataset(args.output)
    print(f"Wrote Synthetic demonstration dataset to {args.output}")


if __name__ == "__main__":
    main()
