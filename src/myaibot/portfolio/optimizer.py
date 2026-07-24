"""Final portfolio ensemble/optimizer.

This layer combines lab signals into target long-only holdings. It is deliberately
not an alpha model; it is an auditable portfolio construction policy. GPT-5.5 lab
managers can feed it signals/trade intents, but sizing and constraint checks stay
deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from myaibot.core.models import MarketSnapshot, PortfolioState, TradeIntent, TradeSignal


@dataclass(frozen=True)
class EnsembleConfig:
    lab_weights: dict[str, float] = field(
        default_factory=lambda: {
            "static_momentum": 0.70,
            "relay_relative_strength": 0.55,
            "doxa_social_attention": 0.10,
            "logos_fundamentals": 0.20,
            "people_employee_sentiment": 0.05,
            "thread_news_events": 0.10,
        }
    )
    max_names: int = 20
    max_weight_per_name: float = 0.05
    min_combined_score: float = 0.0
    min_confidence: float = 0.25
    rebalance_threshold_weight: float = 0.005
    close_unranked_positions: bool = True
    default_horizon_days: int = 20
    originator: str = "ensemble.portfolio_manager.gpt-5.5-supervised"


@dataclass
class CombinedSignal:
    ticker: str
    score: float
    confidence: float
    expected_net_edge_bps: float | None
    labs: list[str]
    signals: list[str]


class EnsemblePortfolioManager:
    def __init__(self, config: EnsembleConfig | None = None) -> None:
        self.config = config or EnsembleConfig()

    def combine(self, signals: list[TradeSignal]) -> list[CombinedSignal]:
        buckets: dict[str, dict[str, object]] = {}
        for signal in signals:
            if signal.direction != "long" or signal.confidence < self.config.min_confidence:
                continue
            weight = self.config.lab_weights.get(signal.lab_id, 0.0)
            if weight <= 0:
                continue
            bucket = buckets.setdefault(
                signal.ticker,
                {"score": 0.0, "weight": 0.0, "confidence": 0.0, "edge": None, "labs": [], "signals": []},
            )
            contribution = weight * max(signal.score, 0.0) * signal.confidence
            bucket["score"] = float(bucket["score"]) + contribution
            bucket["weight"] = float(bucket["weight"]) + weight
            bucket["confidence"] = max(float(bucket["confidence"]), signal.confidence)
            if signal.expected_net_edge_bps is not None:
                current = bucket["edge"]
                bucket["edge"] = signal.expected_net_edge_bps if current is None else max(float(current), signal.expected_net_edge_bps)
            cast_labs = bucket["labs"]
            cast_signals = bucket["signals"]
            assert isinstance(cast_labs, list) and isinstance(cast_signals, list)
            cast_labs.append(signal.lab_id)
            cast_signals.append(signal.signal_id)

        combined: list[CombinedSignal] = []
        for ticker, bucket in buckets.items():
            raw_score = float(bucket["score"])
            if raw_score <= self.config.min_combined_score:
                continue
            combined.append(
                CombinedSignal(
                    ticker=ticker,
                    score=raw_score,
                    confidence=float(bucket["confidence"]),
                    expected_net_edge_bps=None if bucket["edge"] is None else float(bucket["edge"]),
                    labs=list(bucket["labs"]),  # type: ignore[arg-type]
                    signals=list(bucket["signals"]),  # type: ignore[arg-type]
                )
            )
        combined.sort(key=lambda item: (item.score, item.confidence), reverse=True)
        return combined[: self.config.max_names]

    def target_weights(self, combined: list[CombinedSignal], market: MarketSnapshot) -> dict[str, float]:
        if not combined:
            return {}
        total_score = sum(max(item.score, 0.0) for item in combined)
        if total_score <= 0:
            return {}
        gross_budget = min(1.0, market.regime_multiplier)
        weights: dict[str, float] = {}
        for item in combined:
            raw = gross_budget * max(item.score, 0.0) / total_score
            weights[item.ticker] = min(raw, self.config.max_weight_per_name)
        return weights

    def propose_intents(
        self,
        *,
        signals: list[TradeSignal],
        portfolio: PortfolioState,
        market: MarketSnapshot,
        data_cutoff: datetime,
    ) -> list[TradeIntent]:
        combined = self.combine(signals)
        targets = self.target_weights(combined, market)
        by_ticker = {item.ticker: item for item in combined}
        intents: list[TradeIntent] = []
        equity = max(portfolio.equity, 1e-9)

        for ticker, target_weight in targets.items():
            current_weight = portfolio.position_value(ticker) / equity
            is_new_position = portfolio.position_quantity(ticker) == 0
            if not is_new_position and target_weight - current_weight < self.config.rebalance_threshold_weight:
                continue
            item = by_ticker[ticker]
            intents.append(
                TradeIntent(
                    lab_id="ensemble",
                    originator=self.config.originator,
                    strategy_version="0.1.0",
                    data_cutoff=data_cutoff,
                    ticker=ticker,
                    side="buy",
                    action="buy",
                    target_weight=target_weight,
                    horizon_days=self.config.default_horizon_days,
                    confidence=item.confidence,
                    expected_gross_edge_bps=item.expected_net_edge_bps,
                    expected_net_edge_bps=item.expected_net_edge_bps,
                    thesis=f"Ensemble long target from labs {sorted(set(item.labs))}; combined score {item.score:.4f}.",
                    invalidators=["combined score drops out of ranked book", "risk validator rejects", "regime multiplier falls sharply"],
                    metadata={"combined_score": item.score, "labs": item.labs, "signals": item.signals, "target_weight": target_weight},
                )
            )

        if self.config.close_unranked_positions:
            for ticker, pos in portfolio.positions.items():
                if pos.quantity <= 0:
                    continue
                target_weight = targets.get(ticker, 0.0)
                current_weight = portfolio.position_value(ticker) / equity
                if current_weight - target_weight >= self.config.rebalance_threshold_weight:
                    action = "close" if target_weight <= 0 else "trim"
                    intents.append(
                        TradeIntent(
                            lab_id="ensemble",
                            originator=self.config.originator,
                            strategy_version="0.1.0",
                            data_cutoff=data_cutoff,
                            ticker=ticker,
                            side="sell",
                            action=action,
                            target_weight=target_weight,
                            horizon_days=self.config.default_horizon_days,
                            confidence=0.60,
                            expected_gross_edge_bps=None,
                            expected_net_edge_bps=9999.0,  # exits are risk-management actions, not alpha buys
                            thesis="Reduce/close position because it is no longer in the ensemble target book.",
                            invalidators=["fresh approved lab signal restores target weight"],
                            metadata={"current_weight": current_weight, "target_weight": target_weight},
                        )
                    )
        return intents
