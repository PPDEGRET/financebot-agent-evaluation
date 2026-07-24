import pandas as pd
import pytest

from myaibot.agents.hourly_agent import HourlyTradingAgent
from myaibot.backtest.hourly_replay import HourlyReplayEngine
from myaibot.core.models import RiskLimits
from myaibot.data.hourly import daily_to_hourly_previous_close
from myaibot.execution.ledger import PaperLedger
from myaibot.labs.static_momentum import StaticMomentumConfig, StaticMomentumLab
from myaibot.portfolio.optimizer import EnsembleConfig, EnsemblePortfolioManager


class CapturingPolicyAgent(HourlyTradingAgent):
    def __init__(self) -> None:
        super().__init__(mode="policy")
        self.calls = []

    def decide(self, **kwargs):
        self.calls.append(kwargs)
        return super().decide(**kwargs)


def _engine(*, daily, hourly, agent=None, daily_volume=None, rich_context=False):
    return HourlyReplayEngine(
        daily_prices=daily,
        hourly_prices=hourly,
        labs=[StaticMomentumLab(StaticMomentumConfig(lookback_bars=2, top_n=1, freeze_selection=True))],
        portfolio_manager=EnsemblePortfolioManager(EnsembleConfig(max_names=1, max_weight_per_name=0.2, lab_weights={"static_momentum": 1.0})),
        agent=agent or HourlyTradingAgent(mode="policy"),
        ledger=PaperLedger(initial_cash=100000),
        limits=RiskLimits(
            min_adv_dollars=0,
            min_expected_net_edge_bps=-10000,
            max_position_weight=0.5,
            max_order_notional_weight=0.5,
            cost_bps_per_side=0,
            slippage_bps=0,
        ),
        daily_volume=daily_volume,
        rich_context=rich_context,
        context_max_symbols=10,
        context_intraday_bars=5,
    )


def test_hourly_replay_policy_agent_fills_candidate():
    dates = pd.date_range("2024-01-01", periods=280, freq="B")
    daily = pd.DataFrame(
        {
            "SPY": [100 + i * 0.01 for i in range(len(dates))],
            "AAA": [50 + i * 0.20 for i in range(len(dates))],
            "BBB": [80 - i * 0.01 for i in range(len(dates))],
        },
        index=dates,
    )
    hourly = daily_to_hourly_previous_close(daily)
    start = str(hourly.index[-7])
    end = str(hourly.index[-1])
    engine = HourlyReplayEngine(
        daily_prices=daily,
        hourly_prices=hourly,
        labs=[StaticMomentumLab(StaticMomentumConfig(lookback_bars=60, top_n=1, freeze_selection=True))],
        portfolio_manager=EnsemblePortfolioManager(EnsembleConfig(max_names=1, max_weight_per_name=0.2, lab_weights={"static_momentum": 1.0})),
        agent=HourlyTradingAgent(mode="policy"),
        ledger=PaperLedger(initial_cash=100000),
        limits=RiskLimits(min_adv_dollars=0, min_expected_net_edge_bps=-10000, max_position_weight=0.5, max_order_notional_weight=0.5),
    )
    result = engine.run(start=start, end=end, invoke_every_hour=True)
    assert result.agent_decisions
    assert result.fills


def test_hourly_replay_lags_visible_prices_and_fills_next_bar():
    daily_index = pd.to_datetime(["2025-12-29", "2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"])
    daily = pd.DataFrame(
        {
            "SPY": [100, 101, 102, 103, 104],
            "AAA": [50, 60, 70, 80, 90],
            "BBB": [80, 79, 78, 77, 76],
        },
        index=daily_index,
    )
    hourly = pd.DataFrame(
        {
            "SPY": [103, 104, 105, 106],
            # Make the data cutoff, decision bar, and fill bar unmistakable.
            "AAA": [80, 1000, 120, 130],
            "BBB": [77, 76, 75, 74],
        },
        index=pd.to_datetime(["2026-01-01 15:30", "2026-01-02 09:30", "2026-01-02 10:30", "2026-01-02 11:30"]),
    )
    agent = CapturingPolicyAgent()
    engine = _engine(daily=daily, hourly=hourly, agent=agent)

    result = engine.run(start="2026-01-02 09:30", end="2026-01-02 11:30", invoke_every_hour=True)

    assert agent.calls
    first_call = agent.calls[0]
    assert first_call["as_of"] == pd.Timestamp("2026-01-02 09:30").to_pydatetime()
    assert first_call["market_digest"]["data_cutoff"] == pd.Timestamp("2026-01-01 15:30").to_pydatetime().isoformat()
    assert first_call["prices"]["AAA"] == 80
    assert first_call["prices"]["AAA"] != 1000
    assert first_call["prices"]["AAA"] != 120

    first_fill_event = next(event for event in result.events if event.kind == "fill")
    assert first_fill_event.decision_time == pd.Timestamp("2026-01-02 09:30").to_pydatetime()
    assert first_fill_event.data_cutoff == pd.Timestamp("2026-01-01 15:30").to_pydatetime()
    assert first_fill_event.execution_time == pd.Timestamp("2026-01-02 10:30").to_pydatetime()
    assert first_fill_event.execution_time > first_fill_event.decision_time
    assert first_fill_event.payload["price"] == pytest.approx(120)


def test_intraday_symbol_context_excludes_same_day_daily_volume():
    dates = pd.bdate_range("2025-11-25", "2026-01-02")
    daily = pd.DataFrame(
        {
            "SPY": range(100, 100 + len(dates)),
            "AAA": range(50, 50 + len(dates)),
        },
        index=dates,
    )
    daily_volume = pd.DataFrame(
        {
            "SPY": [1000] * len(dates),
            "AAA": [100] * (len(dates) - 1) + [999999],
        },
        index=dates,
    )
    hourly = pd.DataFrame(
        {"SPY": [float(daily["SPY"].iloc[-2])], "AAA": [float(daily["AAA"].iloc[-2])]},
        index=pd.to_datetime(["2026-01-01 15:30"]),
    )
    engine = _engine(daily=daily, hourly=hourly, daily_volume=daily_volume, rich_context=True)
    history = engine._history_for_hour(pd.Timestamp("2026-01-01 15:30"), {"SPY": float(daily["SPY"].iloc[-2]), "AAA": float(daily["AAA"].iloc[-2])})

    context = engine._symbol_context(pd.Timestamp("2026-01-02 09:30"), history, ["AAA"])

    assert context["AAA"]["latest_volume"] == 100
    assert context["AAA"]["latest_volume"] != 999999
