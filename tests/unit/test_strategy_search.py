import pandas as pd

from myaibot.core.models import RiskLimits
from myaibot.experiments.search import CandidateConfig, SearchProtocol, run_search_then_oos


def test_strategy_search_selects_validation_then_reports_oos():
    dates = pd.date_range("2024-01-01", "2026-06-30", freq="B")
    prices = pd.DataFrame(
        {
            "SPY": [100 + i * 0.03 for i in range(len(dates))],
            "AAA": [50 + i * 0.10 for i in range(len(dates))],
            "BBB": [80 - i * 0.01 for i in range(len(dates))],
        },
        index=dates,
    )
    result = run_search_then_oos(
        prices=prices,
        mentions=None,
        protocol=SearchProtocol(validation_start="2025-01-01", validation_end="2025-12-31", oos_start="2026-01-01", oos_end="2026-06-17"),
        initial_cash=100_000,
        limits=RiskLimits(min_adv_dollars=0, min_expected_net_edge_bps=-10_000),
        candidates=[CandidateConfig(relay_lookback=20, relay_top_n=1, relay_min_momentum=0.01)],
    )
    assert result.selected is not None
    assert result.oos is not None
    assert result.oos["metrics"]["fills"] > 0
