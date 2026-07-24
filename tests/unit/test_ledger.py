from datetime import UTC, datetime

import pytest

from myaibot.core.models import OrderRequest
from myaibot.execution.ledger import PaperLedger


def test_paper_ledger_buy_and_sell_long_only():
    ledger = PaperLedger(initial_cash=10_000)
    buy = OrderRequest(intent_id="intent_1", ticker="AAPL", side="buy", quantity=10, expected_price=100)
    fill = ledger.fill_order(buy, 100, datetime(2026, 1, 2, tzinfo=UTC), cost_bps=10, slippage_bps=5)
    assert fill.price == pytest.approx(100.05)
    assert ledger.positions["AAPL"].quantity == 10
    assert ledger.cash < 9_000

    sell = OrderRequest(intent_id="intent_2", ticker="AAPL", side="sell", quantity=4, expected_price=110)
    ledger.fill_order(sell, 110, datetime(2026, 1, 3, tzinfo=UTC), cost_bps=10, slippage_bps=5)
    assert ledger.positions["AAPL"].quantity == 6


def test_paper_ledger_rejects_sell_to_open():
    ledger = PaperLedger(initial_cash=10_000)
    sell = OrderRequest(intent_id="intent_1", ticker="TSLA", side="sell", quantity=1, expected_price=100)
    with pytest.raises(ValueError):
        ledger.fill_order(sell, 100, datetime(2026, 1, 2, tzinfo=UTC))
