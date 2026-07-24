import pandas as pd

from myaibot.data.feature_store import FeatureStore


def test_feature_store_filters_available_at(tmp_path):
    store = FeatureStore(tmp_path)
    frame = pd.DataFrame(
        {
            "as_of": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "available_at": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "ticker": ["A", "B"],
            "value": [1, 2],
        }
    )
    store.write_table("x", frame)
    available = store.read_available("x", "2026-01-03")
    assert list(available["ticker"]) == ["A"]
