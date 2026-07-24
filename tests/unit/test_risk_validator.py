from datetime import UTC, datetime

from myaibot.core.models import MarketSnapshot, PortfolioState, RiskLimits, TradeIntent
from myaibot.risk.validator import RiskValidator


def _intent(**kwargs):
    base = dict(
        lab_id="test_lab",
        originator="agent",
        data_cutoff=datetime(2025, 12, 31, tzinfo=UTC),
        ticker="AAPL",
        side="buy",
        action="buy",
        target_weight=0.10,
        horizon_days=20,
        confidence=0.7,
        expected_net_edge_bps=100,
        thesis="test",
    )
    base.update(kwargs)
    return TradeIntent(**base)


def test_validator_caps_buy_to_position_limit():
    limits = RiskLimits(max_position_weight=0.05, min_adv_dollars=0, min_expected_net_edge_bps=25)
    validator = RiskValidator(limits)
    portfolio = PortfolioState(as_of=datetime(2026, 1, 1, tzinfo=UTC), cash=100_000, positions={})
    market = MarketSnapshot(as_of=portfolio.as_of, prices={"AAPL": 100}, adv_dollars={"AAPL": 1_000_000_000})
    result = validator.validate(_intent(), portfolio, market)
    assert result.approved
    assert result.order is not None
    assert result.order.quantity == 50
    assert any(issue.code == "TARGET_WEIGHT_CAPPED" for issue in result.issues)


def test_validator_rejects_low_edge_buy():
    limits = RiskLimits(min_adv_dollars=0, min_expected_net_edge_bps=25)
    validator = RiskValidator(limits)
    portfolio = PortfolioState(as_of=datetime(2026, 1, 1, tzinfo=UTC), cash=100_000, positions={})
    market = MarketSnapshot(as_of=portfolio.as_of, prices={"AAPL": 100}, adv_dollars={"AAPL": 1_000_000_000})
    result = validator.validate(_intent(expected_net_edge_bps=10), portfolio, market)
    assert not result.approved
    assert any(issue.code == "EDGE_TOO_LOW" for issue in result.issues)


def test_validator_rejects_sell_to_open():
    limits = RiskLimits()
    validator = RiskValidator(limits)
    portfolio = PortfolioState(as_of=datetime(2026, 1, 1, tzinfo=UTC), cash=100_000, positions={})
    market = MarketSnapshot(as_of=portfolio.as_of, prices={"AAPL": 100})
    intent = _intent(side="sell", action="trim", quantity=1, target_weight=None)
    result = validator.validate(intent, portfolio, market)
    assert not result.approved
    assert any(issue.code == "WOULD_SHORT" for issue in result.issues)
