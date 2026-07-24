from myaibot.agents.trading_manager import GptTradingManager


def test_gpt_trading_manager_identity_is_long_only():
    manager = GptTradingManager(lab_id="test", reasoning_effort="medium")
    assert manager.identity.model == "gpt-5.5"
    assert any("No shorts" in rule for rule in manager.identity.hard_rules)
