"""Doxa lab: social-attention hypothesis scaffold.

Consumes optional `context.alt_data['wsb_mentions']` with columns:
`date`, `ticker`, `mention_count` or a date-indexed wide DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from myaibot.core.models import TradeIntent, TradeSignal
from myaibot.labs.base import BaseLab, LabContext


@dataclass(frozen=True)
class DoxaSocialAttentionConfig:
    min_5d_mentions: int = 5
    min_relative_momentum_20d: float = 0.05
    max_3d_return: float = 0.25
    target_weight: float = 0.01
    horizon_days: int = 20
    top_n: int = 1
    benchmark: str = "SPY"


class DoxaSocialAttentionLab(BaseLab):
    def __init__(self, config: DoxaSocialAttentionConfig | None = None) -> None:
        super().__init__(lab_id="doxa_social_attention", strategy_id="cashtag_regime_relative_strength")
        self.config = config or DoxaSocialAttentionConfig()

    def generate_signals(self, context: LabContext) -> list[TradeSignal]:
        mentions = context.alt_data.get("wsb_mentions")
        if mentions is None or context.price_history.empty or len(context.price_history) < 21:
            return []
        wide_mentions = self._mentions_wide(mentions, context)
        if wide_mentions.empty:
            return []

        prices = context.price_history
        latest = prices.iloc[-1]
        p20 = prices.iloc[-20]
        p3 = prices.iloc[-3] if len(prices) >= 3 else p20
        mom20 = latest / p20 - 1.0
        ret3 = latest / p3 - 1.0
        spy_mom20 = float(mom20.get(self.config.benchmark, 0.0))
        mention_5d = wide_mentions.tail(5).sum(axis=0)

        candidates = []
        for ticker, count in mention_5d.items():
            ticker = str(ticker).upper()
            if ticker == self.config.benchmark or ticker not in mom20.index:
                continue
            rel = float(mom20[ticker]) - spy_mom20
            recent_return = float(ret3.get(ticker, 0.0))
            if count >= self.config.min_5d_mentions and rel >= self.config.min_relative_momentum_20d and recent_return < self.config.max_3d_return:
                candidates.append((ticker, float(count), rel, recent_return))
        candidates.sort(key=lambda row: (row[2], row[1]), reverse=True)

        signals: list[TradeSignal] = []
        for ticker, count, rel, recent_return in candidates[: self.config.top_n]:
            signals.append(
                TradeSignal(
                    lab_id=self.lab_id,
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    ticker=ticker,
                    as_of=context.as_of,
                    data_cutoff=context.data_cutoff,
                    horizon_days=self.config.horizon_days,
                    direction="long",
                    score=rel,
                    confidence=max(0.05, min(0.80, 0.45 + rel)),
                    expected_gross_edge_bps=rel * 10_000,
                    expected_net_edge_bps=rel * 10_000 - 35.0,
                    features={"mentions_5d": count, "relative_momentum_20d": rel, "return_3d": recent_return},
                    notes="Post-hoc WSB-derived hypothesis scaffold; must be forward-validated before promotion.",
                )
            )
        return signals

    def propose_intents(self, context: LabContext, signals: list[TradeSignal]) -> list[TradeIntent]:
        return [
            TradeIntent(
                lab_id=self.lab_id,
                originator=f"{self.lab_id}.manager.gpt-5.5-placeholder",
                strategy_version=self.strategy_version,
                data_cutoff=context.data_cutoff,
                ticker=signal.ticker,
                side="buy",
                action="buy",
                target_weight=self.config.target_weight,
                horizon_days=self.config.horizon_days,
                confidence=signal.confidence,
                expected_gross_edge_bps=signal.expected_gross_edge_bps,
                expected_net_edge_bps=signal.expected_net_edge_bps,
                thesis="Cashtag/social attention is rising while price confirms relative strength and avoids parabolic spike.",
                invalidators=["attention collapses", "relative strength turns negative", "post-hoc hypothesis fails forward paper test"],
                evidence_refs=signal.evidence_refs,
                metadata={"signal_id": signal.signal_id, **signal.features},
            )
            for signal in signals
        ]

    def _mentions_wide(self, mentions: pd.DataFrame, context: LabContext) -> pd.DataFrame:
        df = mentions.copy()
        if {"date", "ticker", "mention_count"}.issubset(df.columns):
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] <= pd.Timestamp(context.as_of)]
            if df.empty:
                return pd.DataFrame()
            return df.pivot_table(index="date", columns="ticker", values="mention_count", aggfunc="sum").fillna(0)
        if isinstance(df.index, pd.DatetimeIndex):
            return df[df.index <= pd.Timestamp(context.as_of)].fillna(0)
        return pd.DataFrame()
