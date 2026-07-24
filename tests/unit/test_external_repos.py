import json

import pandas as pd

from myaibot.data.external_repos import load_glassdoor_signal_panel, load_wsb_cached_price_frame, load_wsb_daily_counts


def test_load_wsb_daily_counts_from_explicit_synthetic_export(tmp_path, monkeypatch):
    output = tmp_path / "outputs" / "wsb_mentions"
    output.mkdir(parents=True)
    (output / "wsb_daily_ticker_counts_2026-01.parquet").touch()
    synthetic = pd.DataFrame(
        {"date": ["2026-01-02"], "ticker": ["syn_a"], "mention_count": [3]}
    )
    monkeypatch.setattr(pd, "read_parquet", lambda _: synthetic.copy())

    df = load_wsb_daily_counts(tmp_path, start="2026-01-01", end="2026-01-05")

    assert df.to_dict("records") == [
        {"date": pd.Timestamp("2026-01-02"), "ticker": "SYN_A", "mention_count": 3}
    ]


def test_load_glassdoor_features_strip_labels_from_explicit_synthetic_export(tmp_path):
    path = tmp_path / "data" / "processed"
    path.mkdir(parents=True)
    pd.DataFrame(
        {
            "as_of": ["2026-01-02"],
            "ticker": ["syn_a"],
            "employee_score": [0.4],
            "forward_return": [0.1],
            "label_up": [1],
        }
    ).to_csv(path / "signal_panel.csv", index=False)

    df = load_glassdoor_signal_panel(tmp_path, features_only=True)

    assert df.loc[0, "ticker"] == "SYN_A"
    assert "employee_score" in df.columns
    assert "forward_return" not in df.columns
    assert "label_up" not in df.columns


def test_load_cached_price_frame_from_explicit_synthetic_export(tmp_path):
    cache = tmp_path / "data" / "cache" / "prices"
    cache.mkdir(parents=True)
    timestamps = [int(pd.Timestamp("2026-01-02", tz="UTC").timestamp()), int(pd.Timestamp("2026-01-05", tz="UTC").timestamp())]
    for symbol, closes in {"SYN_A": [10.0, 10.5], "SYN_B": [20.0, 19.5]}.items():
        payload = {
            "chart": {
                "error": None,
                "result": [
                    {
                        "timestamp": timestamps,
                        "indicators": {"adjclose": [{"adjclose": closes}], "quote": [{"close": closes}]},
                    }
                ],
            }
        }
        (cache / f"{symbol}_yahoo_chart_20260102_20260105.json").write_text(json.dumps(payload), encoding="utf-8")

    frame = load_wsb_cached_price_frame(
        tmp_path,
        symbols=["SYN_A", "SYN_B"],
        start="2026-01-01",
        end="2026-01-10",
    )

    assert list(frame.columns) == ["SYN_A", "SYN_B"]
    assert frame.loc[pd.Timestamp("2026-01-05"), "SYN_A"] == 10.5
