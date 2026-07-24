import pandas as pd

from myaibot.backtest.replay import DailyReplayEngine
from myaibot.core.models import RiskLimits
from myaibot.execution.ledger import PaperLedger
from myaibot.labs.relay_relative_strength import RelayRelativeStrengthConfig, RelayRelativeStrengthLab


def test_daily_replay_uses_past_history_and_fills_next_day():
    dates = pd.date_range("2025-09-01", periods=90, freq="B")
    prices = pd.DataFrame(
        {
            "SPY": [100 + i * 0.05 for i in range(len(dates))],
            "AAA": [50 + i * 0.20 for i in range(len(dates))],
            "BBB": [80 - i * 0.02 for i in range(len(dates))],
        },
        index=dates,
    )
    lab = RelayRelativeStrengthLab(RelayRelativeStrengthConfig(lookback_bars=20, top_n=1, min_momentum=0.02, target_weight=0.03))
    engine = DailyReplayEngine(
        price_frame=prices,
        labs=[lab],
        ledger=PaperLedger(initial_cash=100_000),
        limits=RiskLimits(min_adv_dollars=0, min_expected_net_edge_bps=-10_000),
    )
    result = engine.run("2025-10-01", "2025-12-31")
    assert result.signals
    assert result.validations
    assert result.fills
    first_fill_event = next(event for event in result.events if event.kind == "fill")
    assert first_fill_event.execution_time is not None
    assert first_fill_event.execution_time > first_fill_event.decision_time
