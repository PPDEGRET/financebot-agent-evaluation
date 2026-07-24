from datetime import UTC, datetime

from myaibot.agents.hourly_agent import HourlyAgentDecision, decision_to_intents
from myaibot.core.models import PortfolioState, TradeIntent


def test_hourly_decision_approves_candidate_intents():
    now = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    candidate = TradeIntent(
        lab_id="ensemble",
        originator="test",
        data_cutoff=now,
        ticker="AAPL",
        side="buy",
        action="buy",
        target_weight=0.1,
        horizon_days=20,
        confidence=0.7,
        expected_net_edge_bps=100,
        thesis="test",
    )
    decision = HourlyAgentDecision(summary="approve", approved_candidate_ids=[candidate.intent_id], confidence=0.7)
    intents = decision_to_intents(decision, candidate_intents=[candidate], as_of=now, portfolio=PortfolioState(as_of=now, cash=100000, positions={}))
    assert intents == [candidate]
