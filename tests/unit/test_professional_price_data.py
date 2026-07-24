import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_professional_price_data", ROOT / "scripts" / "build_professional_price_data.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
_filter_liquid_symbols = MODULE._filter_liquid_symbols


def test_liquidity_filter_uses_strict_as_of_window_not_future_data():
    dates = pd.bdate_range("2025-12-01", "2026-01-15")
    close = pd.DataFrame(
        {
            "ALREADY_LIQUID": [20.0] * len(dates),
            "FUTURE_PUMP": [2.0 if d < pd.Timestamp("2026-01-05") else 50.0 for d in dates],
        },
        index=dates,
    )
    volume = pd.DataFrame(
        {
            "ALREADY_LIQUID": [1_000_000] * len(dates),
            "FUTURE_PUMP": [1_000 if d < pd.Timestamp("2026-01-05") else 10_000_000 for d in dates],
        },
        index=dates,
    )

    liquid = _filter_liquid_symbols(
        close,
        volume,
        min_price=5.0,
        min_adv_dollars=5_000_000.0,
        min_non_null_ratio=0.50,
        adv_window=5,
        liquidity_as_of="2026-01-02",
    )

    symbols = set(liquid["symbol"])
    assert "ALREADY_LIQUID" in symbols
    assert "FUTURE_PUMP" not in symbols
