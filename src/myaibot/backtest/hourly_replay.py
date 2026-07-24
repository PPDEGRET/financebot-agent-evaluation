"""Market-hour agentic replay.

This is the real FINANCEBOT test loop: at each simulated market hour, build a
timestamp-safe context, ask the agent to approve/submit trades, validate them,
fill approved orders on a later replay bar, then advance.

Safety invariant: agent-visible prices/history/digests are cut off strictly
before the execution bar. This intentionally gives up one bar of freshness so a
close-derived hourly bar can never be used to trade at that same bar's price.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from myaibot.agents.hourly_agent import HourlyAgentDecision, HourlyTradingAgent, decision_to_intents
from myaibot.core.models import MarketSnapshot, RiskLimits, TradeIntent, TradeSignal, ValidationResult
from myaibot.execution.ledger import PaperLedger
from myaibot.labs.atlas_regime import AtlasRegimeLab
from myaibot.labs.base import BaseLab, LabContext
from myaibot.portfolio.optimizer import EnsemblePortfolioManager
from myaibot.risk.validator import RiskValidator


class HourlyReplayEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    as_of: datetime
    kind: str
    ticker: str | None = None
    decision_time: datetime | None = None
    data_cutoff: datetime | None = None
    execution_time: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass
class HourlyReplayResult:
    events: list[HourlyReplayEvent] = field(default_factory=list)
    signals: list[TradeSignal] = field(default_factory=list)
    candidate_intents: list[TradeIntent] = field(default_factory=list)
    approved_intents: list[TradeIntent] = field(default_factory=list)
    agent_decisions: list[HourlyAgentDecision] = field(default_factory=list)
    validations: list[ValidationResult] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fills(self) -> list[dict[str, Any]]:
        return [e.payload for e in self.events if e.kind == "fill"]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "events": [e.model_dump(mode="json") for e in self.events],
            "signals": [s.model_dump(mode="json") for s in self.signals],
            "candidate_intents": [i.model_dump(mode="json") for i in self.candidate_intents],
            "approved_intents": [i.model_dump(mode="json") for i in self.approved_intents],
            "agent_decisions": [d.model_dump(mode="json") for d in self.agent_decisions],
            "validations": [v.model_dump(mode="json") for v in self.validations],
            "snapshots": self.snapshots,
        }


SummonPolicy = Literal["candidate_only", "every_hour", "daily_open", "daily_close", "three_times_day", "open_close", "event_or_review"]


@dataclass
class HourlyReplayEngine:
    daily_prices: pd.DataFrame
    hourly_prices: pd.DataFrame
    labs: list[BaseLab]
    portfolio_manager: EnsemblePortfolioManager
    agent: HourlyTradingAgent
    ledger: PaperLedger
    limits: RiskLimits
    sectors: dict[str, str] = field(default_factory=dict)
    adv_dollars: dict[str, float] = field(default_factory=dict)
    daily_volume: pd.DataFrame | None = None
    symbol_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    atlas_lab: AtlasRegimeLab = field(default_factory=AtlasRegimeLab)
    social_counts: pd.DataFrame | None = None
    write_every_steps: int = 25
    rich_context: bool = False
    context_max_symbols: int = 60
    context_history_days: int = 252
    context_daily_tail: int = 30
    context_intraday_bars: int = 0
    summon_on_candidates: bool = True

    def __post_init__(self) -> None:
        self.daily_prices = self._clean_frame(self.daily_prices, normalize_index=True)
        self.hourly_prices = self._clean_frame(self.hourly_prices, normalize_index=False)
        if self.daily_volume is not None and not self.daily_volume.empty:
            self.daily_volume = self._clean_frame(self.daily_volume, normalize_index=True)
        else:
            self.daily_volume = pd.DataFrame(index=self.daily_prices.index)
        self.symbol_metadata = {str(k).upper(): v for k, v in self.symbol_metadata.items()}
        self.validator = RiskValidator(self.limits)

    def run(
        self,
        *,
        start: str | datetime,
        end: str | datetime,
        out_path: str | Path | None = None,
        max_steps: int | None = None,
        invoke_every_hour: bool = True,
        summon_policy: SummonPolicy | None = None,
        continue_pi_session: bool = True,
    ) -> HourlyReplayResult:
        result = HourlyReplayResult()
        all_hours = self.hourly_prices.index
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        start_pos = int(all_hours.searchsorted(start_ts, side="left"))
        steps = 0
        for pos in range(start_pos, len(all_hours)):
            decision_ts = pd.Timestamp(all_hours[pos])
            if decision_ts > end_ts:
                break
            if pos == 0 or pos + 1 >= len(all_hours):
                continue
            execution_ts = pd.Timestamp(all_hours[pos + 1])
            if execution_ts > end_ts:
                break
            data_ts = pd.Timestamp(all_hours[pos - 1])
            decision_as_of = decision_ts.to_pydatetime()
            data_cutoff = data_ts.to_pydatetime()
            execution_as_of = execution_ts.to_pydatetime()
            prices = self._row_prices(data_ts)
            execution_prices = self._row_prices(execution_ts)
            if not prices or not execution_prices:
                continue
            if max_steps is not None and steps >= max_steps:
                break
            self.ledger.mark_to_market(prices)
            portfolio = self.ledger.snapshot(decision_as_of, prices)
            history = self._history_for_hour(data_ts, prices)
            context = LabContext(
                as_of=decision_as_of,
                data_cutoff=data_cutoff,
                portfolio=portfolio,
                price_history=history,
                alt_data={"wsb_mentions": self._social_available(decision_ts)},
            )
            regime = self.atlas_lab.regime_multiplier(context)
            market = MarketSnapshot(
                as_of=decision_as_of,
                prices=prices,
                adv_dollars={k.upper(): v for k, v in self.adv_dollars.items()},
                sectors={k.upper(): v for k, v in self.sectors.items()},
                regime_multiplier=regime,
            )
            signals = self._generate_signals(context)
            result.signals.extend(signals)
            candidates = self.portfolio_manager.propose_intents(
                signals=signals,
                portfolio=portfolio,
                market=market,
                data_cutoff=data_cutoff,
            )
            result.candidate_intents.extend(candidates)
            digest = self._market_digest(data_ts, prices, history, signals, candidates, regime, portfolio, decision_ts=decision_ts)
            digest["decision_time"] = decision_as_of.isoformat()
            digest["data_cutoff"] = data_cutoff.isoformat()
            digest["execution_price_time"] = execution_as_of.isoformat()
            digest["execution_assumption"] = "Agent sees data through data_cutoff only; approved orders fill on the next replay bar."

            policy = summon_policy or ("every_hour" if invoke_every_hour else "candidate_only")
            should_invoke = self._should_invoke(policy, decision_ts, candidates, portfolio, prices, digest)
            if should_invoke:
                try:
                    decision = self.agent.decide(
                        as_of=decision_as_of,
                        portfolio=portfolio,
                        prices=prices,
                        candidate_intents=candidates,
                        market_digest=digest,
                        continue_session=continue_pi_session and steps > 0,
                    )
                except Exception as exc:
                    decision = HourlyAgentDecision(
                        summary="Agent invocation failed; no_trade for safety.",
                        no_trade_reason="agent_invocation_failed",
                        confidence=0.0,
                        risk_notes=[str(exc)[:2000]],
                    )
            else:
                decision = HourlyAgentDecision(summary="No agent call; no candidates and invoke_every_hour=false.", no_trade_reason="No candidates.")
            result.agent_decisions.append(decision)
            result.events.append(
                HourlyReplayEvent(
                    as_of=decision_as_of,
                    kind="agent_decision",
                    decision_time=decision_as_of,
                    data_cutoff=data_cutoff,
                    execution_time=execution_as_of,
                    payload=decision.model_dump(mode="json"),
                )
            )

            approved = decision_to_intents(decision, candidate_intents=candidates, as_of=decision_as_of, data_cutoff=data_cutoff, portfolio=portfolio)
            result.approved_intents.extend(approved)
            for intent in approved:
                self._process_intent(result, intent, decision_as_of, data_cutoff, execution_as_of, prices, execution_prices, market)

            self.ledger.mark_to_market(execution_prices)
            snapshot = self.ledger.snapshot(execution_as_of, execution_prices)
            result.snapshots.append(
                {
                    "as_of": execution_as_of.isoformat(),
                    "decision_time": decision_as_of.isoformat(),
                    "data_cutoff": data_cutoff.isoformat(),
                    "execution_time": execution_as_of.isoformat(),
                    "cash": snapshot.cash,
                    "equity": snapshot.equity,
                    "market_value": snapshot.market_value,
                    "gross_exposure": snapshot.gross_exposure,
                    "positions": {k: v.model_dump() for k, v in snapshot.positions.items()},
                    "regime_multiplier": regime,
                    "candidate_count": len(candidates),
                    "approved_count": len(approved),
                }
            )
            steps += 1
            if out_path and self.write_every_steps and steps % self.write_every_steps == 0:
                self._write_partial(out_path, result)
        if out_path:
            self._write_partial(out_path, result)
        return result

    def _should_invoke(
        self,
        policy: SummonPolicy,
        ts: pd.Timestamp,
        candidates: list[TradeIntent],
        portfolio: PortfolioState,
        prices: dict[str, float],
        digest: dict[str, Any],
    ) -> bool:
        hour = pd.Timestamp(ts).strftime("%H:%M")
        summon_for_candidates = bool(candidates) and self.summon_on_candidates
        if policy == "every_hour":
            return True
        if policy == "candidate_only":
            return bool(candidates)
        if policy == "daily_open":
            return hour == "09:30" or summon_for_candidates
        if policy == "daily_close":
            return hour == "15:30" or summon_for_candidates
        if policy == "three_times_day":
            return hour in {"09:30", "12:30", "15:30"} or summon_for_candidates
        if policy == "open_close":
            return hour in {"09:30", "15:30"} or summon_for_candidates
        if policy == "event_or_review":
            if summon_for_candidates:
                return True
            if hour in {"09:30", "15:30"} and portfolio.positions:
                return True
            # Intraday event proxy: review when any visible one-step move exceeds 3%.
            one_step = digest.get("top_1d_returns", {}) or {}
            if any(abs(float(v)) >= 0.03 for v in one_step.values()):
                return True
            return False
        return bool(candidates)

    def _generate_signals(self, context: LabContext) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        for lab in self.labs:
            signals.extend(lab.generate_signals(context))
        return signals

    def _process_intent(
        self,
        result: HourlyReplayResult,
        intent: TradeIntent,
        decision_time: datetime,
        data_cutoff: datetime,
        execution_time: datetime,
        prices: dict[str, float],
        execution_prices: dict[str, float],
        market: MarketSnapshot,
    ) -> None:
        validation = self.validator.validate(intent, self.ledger.snapshot(decision_time, prices), market)
        result.validations.append(validation)
        result.events.append(
            HourlyReplayEvent(
                as_of=decision_time,
                kind="validation",
                ticker=intent.ticker,
                decision_time=decision_time,
                data_cutoff=data_cutoff,
                execution_time=execution_time,
                payload=validation.model_dump(mode="json"),
            )
        )
        if not (validation.approved and validation.order is not None):
            return
        price = execution_prices.get(validation.order.ticker)
        if price is None or price <= 0:
            result.events.append(
                HourlyReplayEvent(
                    as_of=execution_time,
                    kind="missed_fill",
                    ticker=validation.order.ticker,
                    decision_time=decision_time,
                    data_cutoff=data_cutoff,
                    execution_time=execution_time,
                    payload={"reason": "missing execution price"},
                )
            )
            return
        try:
            fill = self.ledger.fill_order(
                validation.order,
                price,
                execution_time,
                cost_bps=self.limits.cost_bps_per_side,
                slippage_bps=self.limits.slippage_bps,
            )
            result.events.append(
                HourlyReplayEvent(
                    as_of=execution_time,
                    kind="fill",
                    ticker=fill.ticker,
                    decision_time=decision_time,
                    data_cutoff=data_cutoff,
                    execution_time=execution_time,
                    payload=fill.model_dump(mode="json"),
                )
            )
        except ValueError as exc:
            result.events.append(
                HourlyReplayEvent(
                    as_of=execution_time,
                    kind="fill_rejected_by_ledger",
                    ticker=validation.order.ticker,
                    decision_time=decision_time,
                    data_cutoff=data_cutoff,
                    execution_time=execution_time,
                    payload={"reason": str(exc)},
                )
            )

    def _history_for_hour(self, ts: pd.Timestamp, prices: dict[str, float]) -> pd.DataFrame:
        day = pd.Timestamp(ts).normalize()
        history = self.daily_prices[self.daily_prices.index < day].copy()
        current = pd.DataFrame([prices], index=[pd.Timestamp(ts)])
        common_cols = sorted(set(history.columns).union(current.columns))
        return pd.concat([history.reindex(columns=common_cols), current.reindex(columns=common_cols)]).sort_index()

    def _social_available(self, ts: pd.Timestamp) -> pd.DataFrame:
        if self.social_counts is None or self.social_counts.empty:
            return pd.DataFrame(columns=["date", "ticker", "mention_count"])
        df = self.social_counts.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        # Conservative: today's full daily WSB counts are not available intraday.
        return df[df["date"] < pd.Timestamp(ts).normalize()].copy()

    def _market_digest(
        self,
        ts: pd.Timestamp,
        prices: dict[str, float],
        history: pd.DataFrame,
        signals: list[TradeSignal],
        candidates: list[TradeIntent],
        regime: float,
        portfolio,
        *,
        decision_ts: pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        availability_ts = pd.Timestamp(decision_ts) if decision_ts is not None else pd.Timestamp(ts)
        returns_1d = {}
        returns_20d = {}
        if len(history) >= 2:
            prev = history.iloc[-2]
            latest = history.iloc[-1]
            returns_1d = ((latest / prev) - 1).dropna().sort_values(ascending=False).head(10).round(4).to_dict()
        if len(history) >= 21:
            p20 = history.iloc[-21]
            latest = history.iloc[-1]
            returns_20d = ((latest / p20) - 1).dropna().sort_values(ascending=False).head(10).round(4).to_dict()
        social = self._social_available(availability_ts)
        top_social = []
        if not social.empty:
            recent = social[social["date"] >= availability_ts.normalize() - pd.Timedelta(days=5)]
            if not recent.empty:
                top_social = (
                    recent.groupby("ticker")["mention_count"].sum().sort_values(ascending=False).head(10).astype(int).to_dict()
                )
        digest: dict[str, Any] = {
            "regime_multiplier": regime,
            "top_1d_returns": returns_1d,
            "top_20d_returns": returns_20d,
            "top_5d_social_mentions_available_before_today": top_social,
            "signal_count": len(signals),
            "candidate_count": len(candidates),
            "candidate_tickers": [intent.ticker for intent in candidates[:10]],
        }
        if not self.rich_context:
            return digest

        returns_by_horizon = {
            "1d": self._return_rankings(history, 1),
            "5d": self._return_rankings(history, 5),
            "20d": self._return_rankings(history, 20),
            "63d": self._return_rankings(history, 63),
            "126d": self._return_rankings(history, 126),
            "252d": self._return_rankings(history, 252),
        }
        volume_rankings = self._volume_rankings(availability_ts, history)
        theme_board = self._theme_board(history)
        breadth = self._market_breadth(history)
        selected = self._select_context_symbols(
            prices=prices,
            portfolio=portfolio,
            candidates=candidates,
            returns_by_horizon=returns_by_horizon,
            volume_rankings=volume_rankings,
        )
        benchmark_symbols = [s for s in ["SPY", "QQQ", "IWM", "DIA", "VTI", "TLT", "GLD", "SLV", "USO", "XLE", "URA", "URNM"] if s in prices]
        digest.update(
            {
                "price_universe": {
                    "available_symbol_count": len(prices),
                    "shown_symbol_count": len(selected),
                    "note": "Full replay can trade any ticker with a price in the internal matrix; Pi sees ranked/context-selected symbols only.",
                },
                "ranked_returns": returns_by_horizon,
                "volume_rankings": volume_rankings,
                "market_breadth": breadth,
                "theme_or_sector_board": theme_board,
                "liquid_opportunity_board": self._liquid_opportunity_board(history, prices),
                "visible_latest_price_symbols": selected,
                "symbol_context": self._symbol_context(availability_ts, history, selected, intraday_cutoff_ts=ts),
                "benchmark_context": self._symbol_context(availability_ts, history, benchmark_symbols, intraday_cutoff_ts=ts),
            }
        )
        return digest

    def _return_rankings(self, history: pd.DataFrame, bars: int) -> dict[str, dict[str, float]]:
        if len(history) <= bars:
            return {"top": {}, "bottom": {}}
        latest = history.iloc[-1]
        past = history.iloc[-(bars + 1)]
        returns = ((latest / past) - 1).replace([float("inf"), float("-inf")], pd.NA).dropna().sort_values(ascending=False)
        return {
            "top": returns.head(20).round(4).to_dict(),
            "bottom": returns.tail(20).sort_values().round(4).to_dict(),
        }

    def _select_context_symbols(
        self,
        *,
        prices: dict[str, float],
        portfolio,
        candidates: list[TradeIntent],
        returns_by_horizon: dict[str, dict[str, dict[str, float]]],
        volume_rankings: dict[str, dict[str, float]] | None = None,
    ) -> list[str]:
        selected: list[str] = []

        def add(symbol: str | None) -> None:
            if not symbol:
                return
            s = str(symbol).upper().strip()
            if s in prices and s not in selected:
                selected.append(s)

        for symbol in ["SPY", "QQQ", "IWM", "DIA", "VTI", "TLT", "GLD", "SLV", "USO", "XLE", "URA", "URNM", "AA", "DBB", "XME"]:
            add(symbol)
        for ticker in getattr(portfolio, "positions", {}).keys():
            add(ticker)
        for intent in candidates[: self.context_max_symbols]:
            add(intent.ticker)
        for horizon in ["1d", "5d", "20d", "63d", "126d", "252d"]:
            ranking = returns_by_horizon.get(horizon, {})
            for side in ["top", "bottom"]:
                for ticker in (ranking.get(side, {}) or {}).keys():
                    add(ticker)
                    if len(selected) >= self.context_max_symbols:
                        return selected[: self.context_max_symbols]
        for board in (volume_rankings or {}).values():
            for ticker in (board or {}).keys():
                add(ticker)
                if len(selected) >= self.context_max_symbols:
                    return selected[: self.context_max_symbols]
        return selected[: self.context_max_symbols]

    def _volume_rankings(self, ts: pd.Timestamp, history: pd.DataFrame) -> dict[str, dict[str, float]]:
        if self.daily_volume is None or self.daily_volume.empty:
            return {"top_dollar_volume": {}, "top_volume_vs_20d": {}}
        day = pd.Timestamp(ts).normalize()
        volumes = self.daily_volume[self.daily_volume.index < day]
        if volumes.empty:
            return {"top_dollar_volume": {}, "top_volume_vs_20d": {}}
        price_day = self.daily_prices[self.daily_prices.index < day]
        latest_volume = volumes.iloc[-1].dropna()
        latest_price = price_day.iloc[-1].reindex(latest_volume.index).dropna() if not price_day.empty else pd.Series(dtype=float)
        common = latest_volume.index.intersection(latest_price.index)
        dollar_volume = (latest_volume.reindex(common) * latest_price.reindex(common)).dropna().sort_values(ascending=False)
        avg20 = volumes.reindex(columns=common).tail(20).mean().replace(0, pd.NA)
        rel_volume = (latest_volume.reindex(common) / avg20).replace([float("inf"), float("-inf")], pd.NA).dropna().sort_values(ascending=False)
        return {
            "top_dollar_volume": dollar_volume.head(20).round(0).to_dict(),
            "top_volume_vs_20d": rel_volume.head(20).round(2).to_dict(),
        }

    def _market_breadth(self, history: pd.DataFrame) -> dict[str, Any]:
        if len(history) < 2:
            return {}
        latest = history.iloc[-1].dropna()
        out: dict[str, Any] = {"symbols_counted": int(latest.shape[0])}
        prev = history.iloc[-2].reindex(latest.index).dropna()
        common = latest.index.intersection(prev.index)
        if len(common):
            one = (latest.reindex(common) / prev.reindex(common) - 1.0).dropna()
            out["advancers_1d_pct"] = round(float((one > 0).mean()), 4) if len(one) else None
            out["decliners_1d_pct"] = round(float((one < 0).mean()), 4) if len(one) else None
            out["median_1d_return"] = round(float(one.median()), 4) if len(one) else None
        for window in [20, 50, 200]:
            if len(history) >= window:
                sma = history.tail(window).mean().reindex(latest.index).dropna()
                common = latest.index.intersection(sma.index)
                if len(common):
                    out[f"above_sma{window}_pct"] = round(float((latest.reindex(common) > sma.reindex(common)).mean()), 4)
        if len(history) >= 252:
            rolling = history.tail(252)
            high = rolling.max().reindex(latest.index)
            low = rolling.min().reindex(latest.index)
            out["near_252d_high_pct"] = round(float((latest / high >= 0.95).dropna().mean()), 4)
            out["near_252d_low_pct"] = round(float((latest / low <= 1.05).dropna().mean()), 4)
        return out

    def _theme_board(self, history: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
        if len(history) < 21 or not self.symbol_metadata:
            return {}
        latest = history.iloc[-1]
        ret20 = ((latest / history.iloc[-21]) - 1.0).dropna()
        ret63 = ((latest / history.iloc[-64]) - 1.0).dropna() if len(history) >= 64 else pd.Series(dtype=float)
        rows: dict[str, list[float]] = {}
        for symbol, value in ret20.items():
            meta = self.symbol_metadata.get(str(symbol).upper(), {})
            bucket = str(meta.get("theme") or meta.get("sector") or meta.get("source_bucket") or "unclassified").strip()
            if not bucket or bucket.lower() == "nan":
                bucket = "unclassified"
            rows.setdefault(bucket, []).append(float(value))
        board = []
        for bucket, values in rows.items():
            if len(values) < 2 and bucket == "unclassified":
                continue
            symbols_in_bucket = [s for s in ret20.index if (str(self.symbol_metadata.get(str(s).upper(), {}).get("theme") or self.symbol_metadata.get(str(s).upper(), {}).get("sector") or self.symbol_metadata.get(str(s).upper(), {}).get("source_bucket") or "unclassified").strip() or "unclassified") == bucket]
            top_symbols = ret20.reindex(symbols_in_bucket).sort_values(ascending=False).head(5)
            board.append(
                {
                    "bucket": bucket,
                    "symbols": len(values),
                    "median_ret20": round(float(pd.Series(values).median()), 4),
                    "median_ret63": round(float(ret63.reindex(symbols_in_bucket).dropna().median()), 4) if not ret63.empty else None,
                    "top_symbols_20d": top_symbols.round(4).to_dict(),
                }
            )
        board = sorted(board, key=lambda r: r.get("median_ret20") or -999, reverse=True)
        return {"top": board[:12], "bottom": board[-8:]}

    def _liquid_opportunity_board(self, history: pd.DataFrame, prices: dict[str, float]) -> list[dict[str, Any]]:
        if len(history) < 64:
            return []
        latest = history.iloc[-1]
        ret20 = ((latest / history.iloc[-21]) - 1.0).dropna() if len(history) >= 21 else pd.Series(dtype=float)
        ret63 = ((latest / history.iloc[-64]) - 1.0).dropna()
        dollar_volume = pd.Series({s: self.adv_dollars.get(str(s).upper(), 0.0) for s in ret63.index})
        score = (ret63.rank(pct=True) * 0.55) + (ret20.reindex(ret63.index).fillna(0).rank(pct=True) * 0.30) + (dollar_volume.rank(pct=True) * 0.15)
        rows = []
        for symbol in score.sort_values(ascending=False).head(25).index:
            ticker = str(symbol).upper()
            meta = self.symbol_metadata.get(ticker, {})
            rows.append(
                {
                    "symbol": ticker,
                    "name": meta.get("name"),
                    "theme_or_sector": meta.get("theme") or meta.get("sector") or meta.get("source_bucket"),
                    "latest": round(float(prices.get(ticker, latest.get(symbol))), 4) if pd.notna(latest.get(symbol)) else None,
                    "ret20": round(float(ret20.get(symbol, 0.0)), 4),
                    "ret63": round(float(ret63.get(symbol, 0.0)), 4),
                    "avg_dollar_volume": round(float(self.adv_dollars.get(ticker, 0.0)), 0),
                    "score": round(float(score.get(symbol)), 4),
                }
            )
        return rows

    def _symbol_context(
        self,
        ts: pd.Timestamp,
        history: pd.DataFrame,
        symbols: list[str],
        *,
        intraday_cutoff_ts: pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not symbols:
            return out
        history_days = max(1, self.context_history_days)
        daily_tail = max(0, self.context_daily_tail)
        for symbol in symbols:
            if symbol not in history.columns:
                continue
            series = history[symbol].dropna().tail(history_days + 1)
            if series.empty:
                continue
            latest = float(series.iloc[-1])
            meta = self.symbol_metadata.get(str(symbol).upper(), {})
            stats: dict[str, Any] = {
                "latest": round(latest, 4),
                "name": meta.get("name"),
                "theme": meta.get("theme"),
                "sector": meta.get("sector"),
                "sub_industry": meta.get("sub_industry"),
                "avg_dollar_volume": round(float(self.adv_dollars.get(str(symbol).upper(), 0.0)), 0) if self.adv_dollars else None,
            }
            for bars, label in [(1, "ret_1d"), (5, "ret_5d"), (20, "ret_20d"), (63, "ret_63d"), (126, "ret_126d"), (252, "ret_252d")]:
                if len(series) > bars and float(series.iloc[-(bars + 1)]) > 0:
                    stats[label] = round(latest / float(series.iloc[-(bars + 1)]) - 1.0, 4)
            if len(series) >= 20:
                returns = series.pct_change().dropna().tail(20)
                stats["vol_20d_ann"] = round(float(returns.std()) * (252 ** 0.5), 4) if not returns.empty else None
                stats["sma20_distance"] = round(latest / float(series.tail(20).mean()) - 1.0, 4)
                if self.daily_volume is not None and symbol in self.daily_volume.columns:
                    # Daily volume is only known after the trading day is over. For
                    # any intraday decision, today's full-day volume is future data.
                    volume_day = pd.Timestamp(ts).normalize()
                    vol_series = self.daily_volume[self.daily_volume.index < volume_day][symbol].dropna().tail(21)
                    if len(vol_series) >= 6:
                        latest_vol = float(vol_series.iloc[-1])
                        avg20 = float(vol_series.iloc[:-1].tail(20).mean())
                        stats["latest_volume"] = round(latest_vol, 0)
                        stats["volume_vs_20d"] = round(latest_vol / avg20, 2) if avg20 > 0 else None
            if len(series) >= 50:
                stats["sma50_distance"] = round(latest / float(series.tail(50).mean()) - 1.0, 4)
            if len(series) >= 200:
                stats["sma200_distance"] = round(latest / float(series.tail(200).mean()) - 1.0, 4)
            if len(series) >= 2:
                high = float(series.max())
                low = float(series.min())
                stats["drawdown_from_context_high"] = round(latest / high - 1.0, 4) if high > 0 else None
                stats["distance_from_context_low"] = round(latest / low - 1.0, 4) if low > 0 else None
            if len(series) >= 64:
                for bench in ["SPY", "QQQ"]:
                    if bench in history.columns:
                        bench_series = history[bench].dropna().tail(64)
                        aligned = pd.concat([series.tail(64).pct_change(), bench_series.pct_change()], axis=1, join="inner").dropna()
                        if len(aligned) >= 20:
                            sret = aligned.iloc[:, 0]
                            bret = aligned.iloc[:, 1]
                            bvar = float(bret.var())
                            if bvar > 0:
                                stats[f"beta_63d_vs_{bench.lower()}"] = round(float(sret.cov(bret) / bvar), 3)
                            stats[f"corr_63d_vs_{bench.lower()}"] = round(float(sret.corr(bret)), 3)
                        if len(series) > 63 and len(bench_series) > 63:
                            symbol_ret = latest / float(series.iloc[-64]) - 1.0
                            bench_ret = float(bench_series.iloc[-1]) / float(bench_series.iloc[-64]) - 1.0
                            stats[f"rel_strength_63d_vs_{bench.lower()}"] = round(symbol_ret - bench_ret, 4)
            if daily_tail:
                tail = series.tail(daily_tail)
                stats["daily_closes_tail"] = {idx.strftime("%Y-%m-%d %H:%M"): round(float(value), 4) for idx, value in tail.items()}
            if self.context_intraday_bars > 0 and symbol in self.hourly_prices.columns:
                intraday_cutoff = pd.Timestamp(intraday_cutoff_ts) if intraday_cutoff_ts is not None else pd.Timestamp(ts)
                intraday = self.hourly_prices.loc[:intraday_cutoff, symbol].dropna().tail(self.context_intraday_bars)
                stats["intraday_price_tail"] = {idx.strftime("%Y-%m-%d %H:%M"): round(float(value), 4) for idx, value in intraday.items()}
            out[symbol] = stats
        return out

    def _row_prices(self, ts: pd.Timestamp) -> dict[str, float]:
        row = self.hourly_prices.loc[ts].dropna()
        return {str(k).upper(): float(v) for k, v in row.items() if float(v) > 0}

    def _clean_frame(self, frame: pd.DataFrame, *, normalize_index: bool) -> pd.DataFrame:
        df = frame.copy().sort_index()
        df.index = pd.to_datetime(df.index)
        if normalize_index:
            df.index = df.index.normalize()
        else:
            df.index = df.index.tz_localize(None) if getattr(df.index, "tz", None) is not None else df.index
        df.columns = [str(c).upper() for c in df.columns]
        return df.apply(pd.to_numeric, errors="coerce")

    def _write_partial(self, out_path: str | Path, result: HourlyReplayResult) -> None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_jsonable(), indent=2, default=str), encoding="utf-8")
