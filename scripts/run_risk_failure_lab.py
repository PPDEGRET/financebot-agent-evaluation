#!/usr/bin/env python3
"""Exercise deterministic FINANCEBOT risk failures with synthetic intents."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("FINANCEBOT_SHOWCASE_MODE", "1")

from myaibot.core.models import MarketSnapshot, PortfolioState, Position, RiskLimits, TradeIntent
from myaibot.core.showcase import showcase_mode_enabled
from myaibot.risk.validator import RiskValidator

DEFAULT_OUTPUT = ROOT / "artifacts" / "risk-failure-lab.json"
AS_OF = datetime(2026, 2, 16, 15, 30, tzinfo=UTC)
CUTOFF = datetime(2026, 2, 16, 14, 30, tzinfo=UTC)


def _position(ticker: str, quantity: int, price: float, sector: str) -> Position:
    return Position(ticker=ticker, quantity=quantity, avg_cost=price, last_price=price, sector=sector)


def _portfolio(*, cash: float = 100_000.0, positions: list[Position] | None = None) -> PortfolioState:
    position_map = {position.ticker: position for position in (positions or [])}
    return PortfolioState(as_of=AS_OF, cash=cash, positions=position_map)


def _market(
    prices: dict[str, float],
    *,
    adv: dict[str, float] | None = None,
    sectors: dict[str, str] | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=AS_OF,
        prices=prices,
        adv_dollars=adv or {ticker: 50_000_000.0 for ticker in prices},
        sectors=sectors or {ticker: f"sector_{ticker.lower()}" for ticker in prices},
        regime_multiplier=1.0,
    )


def _intent(
    case_id: str,
    ticker: str,
    *,
    side: str = "buy",
    action: str = "buy",
    quantity: int = 50,
    edge_bps: float = 80.0,
    cost_bps: float = 10.0,
) -> TradeIntent:
    return TradeIntent(
        intent_id=f"intent_{case_id}",
        lab_id="risk_failure_lab",
        created_at=AS_OF,
        ticker=ticker,
        side=side,
        action=action,
        quantity=quantity,
        confidence=0.8,
        expected_gross_edge_bps=edge_bps,
        expected_net_edge_bps=edge_bps - cost_bps,
        horizon_days=5,
        thesis=f"Synthetic failure-lab intent for {case_id}.",
        originator="showcase.risk_failure_lab",
        data_cutoff=CUTOFF,
    )


def _cases() -> list[dict[str, Any]]:
    empty = _portfolio()
    prices = {"SYN_SAFE": 100.0}
    return [
        {
            "id": "sell_to_open",
            "label": "Sell-to-open attempt",
            "intent": _intent("sell_to_open", "SYN_SAFE", side="sell", action="trim", quantity=10),
            "portfolio": empty,
            "market": _market(prices),
            "expected": ["WOULD_SHORT"],
        },
        {
            "id": "missing_price",
            "label": "Missing current price",
            "intent": _intent("missing_price", "SYN_MISSING"),
            "portfolio": empty,
            "market": _market(prices),
            "expected": ["MISSING_PRICE"],
        },
        {
            "id": "price_floor",
            "label": "Price below floor",
            "intent": _intent("price_floor", "SYN_PENNY", quantity=100),
            "portfolio": empty,
            "market": _market({"SYN_PENNY": 0.5}),
            "expected": ["PRICE_TOO_LOW"],
        },
        {
            "id": "liquidity_floor",
            "label": "Liquidity below floor",
            "intent": _intent("liquidity_floor", "SYN_THIN"),
            "portfolio": empty,
            "market": _market({"SYN_THIN": 100.0}, adv={"SYN_THIN": 1_000_000.0}),
            "expected": ["LIQUIDITY_TOO_LOW"],
        },
        {
            "id": "weak_edge",
            "label": "Expected edge below cost hurdle",
            "intent": _intent("weak_edge", "SYN_WEAK", edge_bps=30.0, cost_bps=10.0),
            "portfolio": empty,
            "market": _market({"SYN_WEAK": 100.0}),
            "expected": ["EDGE_TOO_LOW"],
        },
        {
            "id": "oversized_order",
            "label": "Order notional above cap",
            "intent": _intent("oversized_order", "SYN_LARGE", quantity=250),
            "portfolio": empty,
            "market": _market({"SYN_LARGE": 100.0}),
            "expected": ["ORDER_CAP"],
        },
        {
            "id": "insufficient_cash",
            "label": "Cash buffer breach",
            "intent": _intent("insufficient_cash", "SYN_NEW", quantity=50),
            "portfolio": _portfolio(cash=1_000.0, positions=[_position("SYN_RESERVE", 990, 100.0, "reserve")]),
            "market": _market(
                {"SYN_NEW": 100.0, "SYN_RESERVE": 100.0},
                sectors={"SYN_NEW": "new", "SYN_RESERVE": "reserve"},
            ),
            "expected": ["INSUFFICIENT_CASH"],
        },
        {
            "id": "max_positions",
            "label": "Maximum positions reached",
            "intent": _intent("max_positions", "SYN_THREE", quantity=50),
            "portfolio": _portfolio(
                cash=80_000.0,
                positions=[
                    _position("SYN_ONE", 100, 100.0, "one"),
                    _position("SYN_TWO", 100, 100.0, "two"),
                ],
            ),
            "market": _market(
                {"SYN_ONE": 100.0, "SYN_TWO": 100.0, "SYN_THREE": 100.0},
                sectors={"SYN_ONE": "one", "SYN_TWO": "two", "SYN_THREE": "three"},
            ),
            "expected": ["MAX_POSITIONS"],
        },
        {
            "id": "sector_concentration",
            "label": "Sector concentration breach",
            "intent": _intent("sector_concentration", "SYN_CLUSTER_B", quantity=100),
            "portfolio": _portfolio(
                cash=55_000.0,
                positions=[_position("SYN_CLUSTER_A", 450, 100.0, "cluster")],
            ),
            "market": _market(
                {"SYN_CLUSTER_A": 100.0, "SYN_CLUSTER_B": 100.0},
                sectors={"SYN_CLUSTER_A": "cluster", "SYN_CLUSTER_B": "cluster"},
            ),
            "expected": ["SECTOR_CAP"],
        },
        {
            "id": "position_concentration",
            "label": "Single-position cap breach",
            "intent": _intent("position_concentration", "SYN_FOCUS", quantity=100),
            "portfolio": _portfolio(
                cash=65_000.0,
                positions=[_position("SYN_FOCUS", 350, 100.0, "focus")],
            ),
            "market": _market({"SYN_FOCUS": 100.0}, sectors={"SYN_FOCUS": "focus"}),
            "expected": ["POSITION_CAP"],
        },
        {
            "id": "safe_control",
            "label": "Valid long-only control",
            "intent": _intent("safe_control", "SYN_SAFE", quantity=50),
            "portfolio": empty,
            "market": _market(prices),
            "expected": ["APPROVED"],
            "approved": True,
        },
    ]


def build_report() -> dict[str, Any]:
    if not showcase_mode_enabled():
        raise RuntimeError("Refusing to run the failure lab with showcase mode disabled.")
    limits = RiskLimits(
        allow_shorts=False,
        allow_margin=False,
        max_position_weight=0.40,
        max_order_notional_weight=0.20,
        max_sector_weight=0.50,
        max_positions=2,
        cash_buffer_weight=0.05,
        min_price=5.0,
        min_adv_dollars=20_000_000.0,
        min_expected_net_edge_bps=25.0,
        cost_bps_per_side=4.0,
        slippage_bps=3.0,
    )
    validator = RiskValidator(limits)
    rendered_cases: list[dict[str, Any]] = []
    for case in _cases():
        result = validator.validate(case["intent"], case["portfolio"], case["market"])
        issue_codes = [issue.code for issue in result.issues if issue.severity == "error"]
        if result.approved:
            issue_codes = [issue.code for issue in result.issues if issue.code == "APPROVED"]
        if issue_codes != case["expected"] or result.approved is not bool(case.get("approved", False)):
            raise RuntimeError(f"Failure-lab case {case['id']} produced {issue_codes}, expected {case['expected']}.")
        rendered_cases.append(
            {
                "id": case["id"],
                "label": case["label"],
                "approved": result.approved,
                "intent": {
                    "ticker": case["intent"].ticker,
                    "side": case["intent"].side,
                    "action": case["intent"].action,
                    "quantity": case["intent"].quantity,
                    "expected_net_edge_bps": case["intent"].expected_net_edge_bps,
                },
                "portfolio_before": {
                    "cash": round(case["portfolio"].cash, 2),
                    "equity": round(case["portfolio"].equity, 2),
                    "positions": len(case["portfolio"].positions),
                },
                "issue_codes": [issue.code for issue in result.issues],
                "issues": [
                    {"severity": issue.severity, "code": issue.code, "message": issue.message}
                    for issue in result.issues
                ],
                "order_created": result.order is not None,
            }
        )

    blocked = sum(not case["approved"] for case in rendered_cases)
    return {
        "schema_version": 1,
        "label": "Synthetic demonstration",
        "mode": "real_deterministic_validator",
        "as_of": AS_OF.isoformat(),
        "blocked_cases": blocked,
        "approved_controls": sum(case["approved"] for case in rendered_cases),
        "external_model_calls": 0,
        "broker_connections": 0,
        "limits": limits.model_dump(mode="json"),
        "cases": rendered_cases,
        "caveat": "These fixed synthetic intents demonstrate enforcement behavior, not the safety or quality of any investment decision.",
    }


def _serialized(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Compare fresh validator results with the committed artifact.")
    args = parser.parse_args()

    rendered = _serialized(build_report())
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"Risk failure-lab check failed: regenerate {args.output}")
        print("Risk failure-lab check passed: committed rejections match the real validator.")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
