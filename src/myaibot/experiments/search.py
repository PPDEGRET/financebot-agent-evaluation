"""Strategy search and promotion pipeline.

This module enforces the protocol: tune on validation only, then run frozen OOS
once with the selected config. OOS performance is reported but not fed back into
selection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import product
from typing import Any

import pandas as pd

from myaibot.backtest.metrics import benchmark_return, equity_curve_from_snapshots, performance_summary
from myaibot.backtest.replay import DailyReplayEngine
from myaibot.core.models import RiskLimits
from myaibot.execution.ledger import PaperLedger
from myaibot.labs.doxa_social_attention import DoxaSocialAttentionConfig, DoxaSocialAttentionLab
from myaibot.labs.relay_relative_strength import RelayRelativeStrengthConfig, RelayRelativeStrengthLab
from myaibot.labs.static_momentum import StaticMomentumConfig, StaticMomentumLab
from myaibot.portfolio.optimizer import EnsembleConfig, EnsemblePortfolioManager


@dataclass(frozen=True)
class SearchProtocol:
    validation_start: str = "2025-01-01"
    validation_end: str = "2025-12-31"
    oos_start: str = "2026-01-01"
    oos_end: str = "2026-06-17"
    benchmark: str = "SPY"


@dataclass(frozen=True)
class CandidateConfig:
    use_static_momentum: bool = True
    static_lookback: int = 252
    static_top_n: int = 5
    static_min_return: float = 0.0
    static_freeze_selection: bool = False
    use_relay: bool = True
    relay_lookback: int = 60
    relay_top_n: int = 5
    relay_min_momentum: float = 0.05
    relay_target_weight: float = 0.03
    doxa_min_5d_mentions: int = 5
    doxa_min_rel_mom20: float = 0.05
    doxa_target_weight: float = 0.01
    ensemble_max_names: int = 15
    ensemble_max_weight: float = 0.05
    ensemble_rebalance_threshold: float = 0.005
    ensemble_close_unranked: bool = True


@dataclass
class SearchResult:
    protocol: SearchProtocol
    candidates: list[dict[str, Any]] = field(default_factory=list)
    selected: dict[str, Any] | None = None
    oos: dict[str, Any] | None = None
    warning: str = "Frozen OOS result is report-only and must not be used to select this run's config."


def default_candidate_grid() -> list[CandidateConfig]:
    values = []
    for static_lookback, static_top_n, lookback, top_n, min_mom, max_names, max_weight in product(
        [126, 252],
        [3, 5],
        [20, 60, 120],
        [3, 5, 10],
        [0.02, 0.05, 0.10],
        [5, 10, 20],
        [0.05, 0.10, 0.20],
    ):
        values.append(
            CandidateConfig(
                static_lookback=static_lookback,
                static_top_n=static_top_n,
                relay_lookback=lookback,
                relay_top_n=top_n,
                relay_min_momentum=min_mom,
                ensemble_max_names=max_names,
                ensemble_max_weight=max_weight,
            )
        )
    return values


def run_candidate(
    *,
    prices: pd.DataFrame,
    mentions: pd.DataFrame | None,
    candidate: CandidateConfig,
    start: str,
    end: str,
    initial_cash: float,
    limits: RiskLimits,
) -> dict[str, Any]:
    labs = []
    if candidate.use_static_momentum:
        labs.append(
            StaticMomentumLab(
                StaticMomentumConfig(
                    lookback_bars=candidate.static_lookback,
                    top_n=candidate.static_top_n,
                    min_return=candidate.static_min_return,
                    target_weight=candidate.ensemble_max_weight,
                    freeze_selection=candidate.static_freeze_selection,
                )
            )
        )
    if candidate.use_relay:
        labs.append(
            RelayRelativeStrengthLab(
                RelayRelativeStrengthConfig(
                    lookback_bars=candidate.relay_lookback,
                    top_n=candidate.relay_top_n,
                    min_momentum=candidate.relay_min_momentum,
                    target_weight=candidate.relay_target_weight,
                )
            )
        )
    if mentions is not None and not mentions.empty:
        labs.append(
            DoxaSocialAttentionLab(
                DoxaSocialAttentionConfig(
                    min_5d_mentions=candidate.doxa_min_5d_mentions,
                    min_relative_momentum_20d=candidate.doxa_min_rel_mom20,
                    target_weight=candidate.doxa_target_weight,
                )
            )
        )
    manager = EnsemblePortfolioManager(
        EnsembleConfig(
            max_names=candidate.ensemble_max_names,
            max_weight_per_name=candidate.ensemble_max_weight,
            rebalance_threshold_weight=candidate.ensemble_rebalance_threshold,
            close_unranked_positions=candidate.ensemble_close_unranked,
        )
    )
    engine = DailyReplayEngine(
        price_frame=prices,
        labs=labs,
        ledger=PaperLedger(initial_cash=initial_cash),
        limits=limits,
        alt_data={"wsb_mentions": mentions} if mentions is not None else {},
        portfolio_manager=manager,
    )
    replay = engine.run(start, end)
    equity = equity_curve_from_snapshots(replay.snapshots)
    metrics = performance_summary(equity, initial_cash=initial_cash)
    spy = benchmark_return(prices, "SPY", start, end)
    qqq = benchmark_return(prices, "QQQ", start, end)
    metrics["benchmark_spy_return"] = 0.0 if spy is None else spy
    metrics["benchmark_qqq_return"] = 0.0 if qqq is None else qqq
    metrics["excess_vs_spy"] = metrics["total_return"] - metrics["benchmark_spy_return"]
    metrics["excess_vs_qqq"] = metrics["total_return"] - metrics["benchmark_qqq_return"]
    metrics["fills"] = len(replay.fills)
    metrics["signals"] = len(replay.signals)
    final_positions = replay.snapshots[-1].get("positions", {}) if replay.snapshots else {}
    return {"candidate": asdict(candidate), "metrics": metrics, "final_positions": final_positions}


def run_search_then_oos(
    *,
    prices: pd.DataFrame,
    mentions: pd.DataFrame | None,
    protocol: SearchProtocol,
    initial_cash: float,
    limits: RiskLimits,
    candidates: list[CandidateConfig] | None = None,
) -> SearchResult:
    grid = candidates or default_candidate_grid()
    result = SearchResult(protocol=protocol)
    for candidate in grid:
        row = run_candidate(
            prices=prices,
            mentions=mentions,
            candidate=candidate,
            start=protocol.validation_start,
            end=protocol.validation_end,
            initial_cash=initial_cash,
            limits=limits,
        )
        result.candidates.append(row)
    result.candidates.sort(
        key=lambda row: (
            row["metrics"].get("excess_vs_spy", -999),
            row["metrics"].get("sharpe", -999),
            -abs(row["metrics"].get("max_drawdown", 999)),
        ),
        reverse=True,
    )
    result.selected = result.candidates[0] if result.candidates else None
    if result.selected:
        selected_candidate = CandidateConfig(**result.selected["candidate"])
        result.oos = run_candidate(
            prices=prices,
            mentions=mentions,
            candidate=selected_candidate,
            start=protocol.oos_start,
            end=protocol.oos_end,
            initial_cash=initial_cash,
            limits=limits,
        )
    return result
