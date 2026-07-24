"""Deterministic long-only risk validation.

The validator is deliberately boring: it has no alpha opinion. It converts a
structured agent intent into a bounded order or rejects it with auditable reasons.
"""

from __future__ import annotations

from dataclasses import dataclass

from myaibot.core.models import (
    MarketSnapshot,
    OrderRequest,
    PortfolioState,
    RiskLimits,
    TradeIntent,
    ValidationIssue,
    ValidationResult,
)


@dataclass(frozen=True)
class RiskValidator:
    limits: RiskLimits

    def validate(
        self,
        intent: TradeIntent,
        portfolio: PortfolioState,
        market: MarketSnapshot,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []

        if intent.side in {"hold", "no_trade"} or intent.action in {"hold", "no_trade"}:
            issues.append(ValidationIssue(severity="info", code="NO_ORDER", message="Intent is hold/no_trade."))
            return ValidationResult(intent_id=intent.intent_id, approved=True, issues=issues, order=None)

        if intent.side == "sell" and intent.action not in {"trim", "close"}:
            issues.append(ValidationIssue(severity="error", code="SELL_TO_OPEN_FORBIDDEN", message="Only trim/close sells are allowed."))

        price = market.price(intent.ticker)
        if price is None or price <= 0:
            issues.append(ValidationIssue(severity="error", code="MISSING_PRICE", message=f"No usable price for {intent.ticker}."))
            return ValidationResult(intent_id=intent.intent_id, approved=False, issues=issues)

        if price < self.limits.min_price and intent.side == "buy":
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="PRICE_TOO_LOW",
                    message=f"{intent.ticker} price {price:.2f} below min {self.limits.min_price:.2f}.",
                )
            )

        adv = market.adv(intent.ticker)
        if intent.side == "buy" and adv is not None and adv < self.limits.min_adv_dollars:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="LIQUIDITY_TOO_LOW",
                    message=f"{intent.ticker} ADV ${adv:,.0f} below min ${self.limits.min_adv_dollars:,.0f}.",
                )
            )

        if intent.side == "buy":
            net_edge = intent.expected_net_edge_bps
            if net_edge is None:
                issues.append(ValidationIssue(severity="warning", code="MISSING_EDGE", message="No expected net edge supplied."))
            elif net_edge < self.limits.min_expected_net_edge_bps:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="EDGE_TOO_LOW",
                        message=f"Expected net edge {net_edge:.1f} bps below minimum {self.limits.min_expected_net_edge_bps:.1f} bps.",
                    )
                )

        if issues and any(issue.severity == "error" for issue in issues):
            return ValidationResult(intent_id=intent.intent_id, approved=False, issues=issues)

        quantity = self._quantity(intent, portfolio, market, price, issues)
        if quantity <= 0:
            issues.append(ValidationIssue(severity="info", code="ZERO_QUANTITY", message="No order required after sizing."))
            return ValidationResult(intent_id=intent.intent_id, approved=True, issues=issues, order=None)

        notional = quantity * price
        fee_estimate = notional * self.limits.cost_bps_per_side / 10_000
        slippage_estimate = notional * self.limits.slippage_bps / 10_000

        if intent.side == "buy":
            self._validate_buy_exposures(intent, portfolio, market, price, quantity, fee_estimate, slippage_estimate, issues)
        elif intent.side == "sell":
            current_qty = portfolio.position_quantity(intent.ticker)
            if quantity > current_qty and not self.limits.allow_shorts:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="WOULD_SHORT",
                        message=f"Sell quantity {quantity} exceeds current long position {current_qty}.",
                    )
                )

        if any(issue.severity == "error" for issue in issues):
            return ValidationResult(intent_id=intent.intent_id, approved=False, issues=issues)

        order = OrderRequest(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,  # type: ignore[arg-type]
            quantity=quantity,
            order_type="limit" if intent.limit_price else "market_on_close",
            limit_price=intent.limit_price,
            expected_price=price,
            estimated_fees=fee_estimate,
            estimated_slippage=slippage_estimate,
            reason=intent.thesis[:240],
        )
        issues.append(ValidationIssue(severity="info", code="APPROVED", message="Order passed deterministic risk checks."))
        return ValidationResult(intent_id=intent.intent_id, approved=True, issues=issues, order=order)

    def _quantity(
        self,
        intent: TradeIntent,
        portfolio: PortfolioState,
        market: MarketSnapshot,
        price: float,
        issues: list[ValidationIssue],
    ) -> int:
        if intent.quantity is not None:
            return intent.quantity

        if intent.target_weight is None:
            return 0

        equity = max(portfolio.equity, 0.0)
        current_value = portfolio.position_value(intent.ticker)
        requested_weight = intent.target_weight * market.regime_multiplier
        capped_weight = min(requested_weight, self.limits.max_position_weight)
        if capped_weight < requested_weight:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="TARGET_WEIGHT_CAPPED",
                    message=f"Target weight {requested_weight:.2%} capped to {capped_weight:.2%}.",
                )
            )
        target_value = capped_weight * equity
        delta_value = target_value - current_value

        if intent.side == "buy":
            return max(int(delta_value // price), 0)
        if intent.side == "sell":
            desired_sell_value = max(current_value - target_value, 0.0)
            return min(int(desired_sell_value // price), portfolio.position_quantity(intent.ticker))
        return 0

    def _validate_buy_exposures(
        self,
        intent: TradeIntent,
        portfolio: PortfolioState,
        market: MarketSnapshot,
        price: float,
        quantity: int,
        fee_estimate: float,
        slippage_estimate: float,
        issues: list[ValidationIssue],
    ) -> None:
        equity = portfolio.equity
        if equity <= 0:
            issues.append(ValidationIssue(severity="error", code="NO_EQUITY", message="Portfolio equity is not positive."))
            return

        notional = quantity * price
        if notional / equity > self.limits.max_order_notional_weight + 1e-9:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="ORDER_CAP",
                    message=f"Order notional weight {notional / equity:.2%} exceeds cap {self.limits.max_order_notional_weight:.2%}.",
                )
            )

        total_cost = notional + fee_estimate + slippage_estimate
        cash_buffer = self.limits.cash_buffer_weight * equity
        if not self.limits.allow_margin and total_cost > max(portfolio.cash - cash_buffer, 0.0):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="INSUFFICIENT_CASH",
                    message=f"Order cost ${total_cost:,.2f} exceeds available cash after buffer.",
                )
            )

        post_value = portfolio.position_value(intent.ticker) + notional
        if post_value / equity > self.limits.max_position_weight + 1e-9:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="POSITION_CAP",
                    message=f"Post-trade position weight {post_value / equity:.2%} exceeds cap {self.limits.max_position_weight:.2%}.",
                )
            )

        if not portfolio.position_quantity(intent.ticker) and len(portfolio.positions) >= self.limits.max_positions:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="MAX_POSITIONS",
                    message=f"Opening {intent.ticker} would exceed max positions {self.limits.max_positions}.",
                )
            )

        sector = market.sector(intent.ticker)
        if sector:
            sector_value = sum(
                pos.market_value for ticker, pos in portfolio.positions.items() if market.sector(ticker) == sector
            ) + notional
            if sector_value / equity > self.limits.max_sector_weight + 1e-9:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="SECTOR_CAP",
                        message=f"Post-trade {sector} exposure {sector_value / equity:.2%} exceeds cap {self.limits.max_sector_weight:.2%}.",
                    )
                )
