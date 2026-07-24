from datetime import UTC, datetime

from myaibot.core.models import MarketSnapshot, PortfolioState, TradeSignal
from myaibot.portfolio.optimizer import EnsembleConfig, EnsemblePortfolioManager


def test_ensemble_optimizer_creates_buy_intent_from_signal():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    signal = TradeSignal(
        lab_id="relay_relative_strength",
        strategy_id="test",
        ticker="AAPL",
        as_of=now,
        data_cutoff=now,
        horizon_days=20,
        direction="long",
        score=0.10,
        confidence=0.8,
        expected_net_edge_bps=100,
    )
    manager = EnsemblePortfolioManager(EnsembleConfig(max_names=5, max_weight_per_name=0.05))
    intents = manager.propose_intents(
        signals=[signal],
        portfolio=PortfolioState(as_of=now, cash=100_000, positions={}),
        market=MarketSnapshot(as_of=now, prices={"AAPL": 100}, regime_multiplier=1.0),
        data_cutoff=now,
    )
    assert len(intents) == 1
    assert intents[0].ticker == "AAPL"
    assert intents[0].side == "buy"
    assert intents[0].target_weight == 0.05
