#!/usr/bin/env python3
"""Verify that showcase mode blocks external model, network, and broker paths."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("FINANCEBOT_SHOWCASE_MODE", "1")

from myaibot.agents.codex_cli import CodexCliRunner
from myaibot.agents.pi_cli import PiCliRunner
from myaibot.core.models import OrderRequest
from myaibot.core.showcase import ShowcaseSafetyError, showcase_mode_enabled
from myaibot.data.hourly import HourlyDownloadConfig, download_yfinance_hourly_frame
from myaibot.execution.ibkr_adapter import IbAsyncBrokerAdapter, IbkrConnectionConfig


def _must_block(label: str, action) -> None:
    try:
        action()
    except ShowcaseSafetyError:
        return
    raise AssertionError(f"Showcase safety failure: {label} was not blocked")


def main() -> None:
    if not showcase_mode_enabled():
        raise SystemExit("Safety check requires FINANCEBOT_SHOWCASE_MODE=1 (the default).")

    adapter = IbAsyncBrokerAdapter(config=IbkrConnectionConfig(transmit_orders=True))
    order = OrderRequest(
        intent_id="showcase-safety-check",
        ticker="SYN_ORBIT",
        side="buy",
        quantity=1,
        expected_price=10.0,
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    checks = {
        "broker connect": adapter.connect,
        "broker disconnect": adapter.disconnect,
        "broker submit": lambda: adapter.submit_order(order),
        "broker reconcile": adapter.reconcile,
        "broker open-order lookup": adapter.open_order_ids,
        "Pi/model invocation": lambda: PiCliRunner().run_text("blocked"),
        "Codex/model invocation": lambda: CodexCliRunner().run_text("blocked"),
        "network market-data download": lambda: download_yfinance_hourly_frame(
            ["SYN_ORBIT"], HourlyDownloadConfig(start="2026-01-01", end="2026-01-02")
        ),
    }
    for label, action in checks.items():
        _must_block(label, action)

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "ib_async" in pyproject or "yfinance" in pyproject:
        raise AssertionError("Side-effecting broker/data clients must not be install dependencies of the showcase.")
    print(f"Showcase safety check passed: {len(checks)} external/live capability paths blocked.")


if __name__ == "__main__":
    main()
