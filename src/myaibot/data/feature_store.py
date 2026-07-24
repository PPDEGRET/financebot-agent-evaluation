"""Local timestamp-safe feature store.

The feature store is intentionally simple: append/read Parquet or CSV files with
`as_of` and `available_at` columns. Replays must filter on `available_at`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


REQUIRED_CLOCK_COLUMNS = {"as_of", "available_at"}


class FeatureStore:
    def __init__(self, root: str | Path = "data/features") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_table(self, name: str, frame: pd.DataFrame) -> Path:
        self._validate_clock(frame)
        path = self.root / f"{name}.parquet"
        try:
            frame.to_parquet(path, index=False)
        except Exception:
            path = self.root / f"{name}.csv"
            frame.to_csv(path, index=False)
        return path

    def read_table(self, name: str) -> pd.DataFrame:
        parquet = self.root / f"{name}.parquet"
        csv = self.root / f"{name}.csv"
        if parquet.exists():
            return pd.read_parquet(parquet)
        if csv.exists():
            return pd.read_csv(csv, parse_dates=["as_of", "available_at"])
        raise FileNotFoundError(f"No feature table {name!r} in {self.root}")

    def read_available(self, name: str, decision_time: str | pd.Timestamp) -> pd.DataFrame:
        df = self.read_table(name)
        self._validate_clock(df)
        cutoff = pd.Timestamp(decision_time)
        return df[pd.to_datetime(df["available_at"]) <= cutoff].copy()

    def append_table(self, name: str, frame: pd.DataFrame, *, dedupe_keys: list[str] | None = None) -> Path:
        self._validate_clock(frame)
        try:
            existing = self.read_table(name)
            combined = pd.concat([existing, frame], ignore_index=True)
            if dedupe_keys:
                combined = combined.drop_duplicates(dedupe_keys, keep="last")
        except FileNotFoundError:
            combined = frame
        return self.write_table(name, combined)

    def _validate_clock(self, frame: pd.DataFrame) -> None:
        missing = REQUIRED_CLOCK_COLUMNS - set(frame.columns)
        if missing:
            raise ValueError(f"Feature table missing timestamp columns: {sorted(missing)}")
