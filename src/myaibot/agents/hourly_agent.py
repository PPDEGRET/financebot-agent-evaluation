"""Hourly trading-agent decision layer.

The Pi/GPT-5.5 agent receives a compact, timestamp-safe hourly context and returns
which candidate intents to approve plus optional new long-only intents. A fast
policy mode implements the same interface for regression tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from myaibot.agents.pi_cli import PiCliRunner
from myaibot.core.models import PortfolioState, TradeIntent, new_id
from myaibot.core.showcase import require_external_runtime


AgentMode = Literal["pi", "policy"]
PolicyStyle = Literal["approve_all", "loss_cut_3", "loss_cut_5", "trailing_stop_7", "qqq_fallback"]


class HourlyAgentDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision_id: str = Field(default_factory=lambda: new_id("hour_decision"))
    summary: str = ""
    approved_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    new_trade_intents: list[dict[str, Any]] = Field(default_factory=list)
    close_tickers: list[str] = Field(default_factory=list)
    no_trade_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    memory_update: str = ""
    risk_notes: list[str] = Field(default_factory=list)


@dataclass
class HourlyTradingAgent:
    mode: AgentMode = "policy"
    runner: PiCliRunner | None = None
    max_candidates: int = 8
    max_positions_to_show: int = 12
    max_latest_prices_to_show: int | None = None
    strategy_brief: str = (
        "Primary strategy: long-only, timestamp-safe, profit-maximizing. Use pre-2026 selected baseline as a starting point: "
        "prefer strong 252-day momentum leaders, currently favoring names like GOOGL/AVGO when they remain leaders. "
        "You may override, hold, trim, or no-trade based only on the hourly context."
    )
    persistent_memory: list[str] = field(default_factory=list)
    context_file_dir: str | Path = "research/experiments/hourly_agent/contexts"
    policy_style: PolicyStyle = "approve_all"
    position_highs: dict[str, float] = field(default_factory=dict)

    def decide(
        self,
        *,
        as_of: datetime,
        portfolio: PortfolioState,
        prices: dict[str, float],
        candidate_intents: list[TradeIntent],
        market_digest: dict[str, Any],
        continue_session: bool = True,
    ) -> HourlyAgentDecision:
        if self.mode == "policy":
            return self._policy_decision(candidate_intents, portfolio=portfolio, prices=prices, market_digest=market_digest, as_of=as_of)
        return self._pi_decision(
            as_of=as_of,
            portfolio=portfolio,
            prices=prices,
            candidate_intents=candidate_intents,
            market_digest=market_digest,
            continue_session=continue_session,
        )

    def _policy_decision(
        self,
        candidate_intents: list[TradeIntent],
        *,
        portfolio: PortfolioState,
        prices: dict[str, float],
        market_digest: dict[str, Any],
        as_of: datetime,
    ) -> HourlyAgentDecision:
        approved = [intent.intent_id for intent in candidate_intents if intent.side in {"buy", "sell"}]
        close_tickers: list[str] = []
        new_trade_intents: list[dict[str, Any]] = []
        notes: list[str] = []

        for ticker, pos in portfolio.positions.items():
            price = prices.get(ticker)
            if not price or pos.avg_cost <= 0:
                continue
            self.position_highs[ticker] = max(self.position_highs.get(ticker, price), price)
            ret = price / pos.avg_cost - 1.0
            drawdown_from_high = price / self.position_highs[ticker] - 1.0
            if self.policy_style == "loss_cut_3" and ret <= -0.03:
                close_tickers.append(ticker); notes.append(f"{ticker} loss_cut_3 ret={ret:.2%}")
            elif self.policy_style == "loss_cut_5" and ret <= -0.05:
                close_tickers.append(ticker); notes.append(f"{ticker} loss_cut_5 ret={ret:.2%}")
            elif self.policy_style == "trailing_stop_7" and drawdown_from_high <= -0.07:
                close_tickers.append(ticker); notes.append(f"{ticker} trailing_stop_7 dd_high={drawdown_from_high:.2%}")
            elif self.policy_style == "qqq_fallback" and ret <= -0.04:
                close_tickers.append(ticker); notes.append(f"{ticker} qqq_fallback close ret={ret:.2%}")

        if self.policy_style == "qqq_fallback" and close_tickers and "QQQ" in prices:
            # If a concentrated loser is closed, rotate into QQQ as a long-only opportunity-cost fallback.
            new_trade_intents.append(
                {
                    "ticker": "QQQ",
                    "side": "buy",
                    "action": "buy",
                    "target_weight": 0.50,
                    "horizon_days": 20,
                    "confidence": 0.55,
                    "expected_net_edge_bps": 75,
                    "thesis": "Policy fallback: rotate closed losing concentrated exposure into QQQ benchmark exposure.",
                    "invalidators": ["QQQ weakens versus SPY", "risk validator rejects"],
                }
            )

        return HourlyAgentDecision(
            summary=f"Policy agent style={self.policy_style}.",
            approved_candidate_ids=approved,
            rejected_candidate_ids=[],
            new_trade_intents=new_trade_intents,
            close_tickers=sorted(set(close_tickers)),
            no_trade_reason=None if (approved or close_tickers or new_trade_intents) else "No action under policy.",
            confidence=0.60,
            risk_notes=notes,
            memory_update=f"{as_of.isoformat()} {self.policy_style} notes: {'; '.join(notes[:3])}" if notes else "",
        )

    def _pi_decision(
        self,
        *,
        as_of: datetime,
        portfolio: PortfolioState,
        prices: dict[str, float],
        candidate_intents: list[TradeIntent],
        market_digest: dict[str, Any],
        continue_session: bool,
    ) -> HourlyAgentDecision:
        require_external_runtime("Pi/model invocation")
        runner = self.runner or PiCliRunner()
        context_payload = self._context_payload(
            as_of=as_of,
            portfolio=portfolio,
            prices=prices,
            candidate_intents=candidate_intents,
            market_digest=market_digest,
        )
        context_path = self._write_context_file(as_of, context_payload)
        prompt = self._file_prompt(context_path)
        raw = runner.run_text(prompt, continue_session=continue_session)
        payload = _extract_json(raw)
        if payload is None:
            return HourlyAgentDecision(
                summary="Pi returned non-JSON; defaulted to no_trade for safety.",
                no_trade_reason="Non-JSON Pi output.",
                confidence=0.0,
                risk_notes=[raw[:1000]],
            )
        payload.setdefault("summary", "Pi hourly decision.")
        if payload.get("no_trade") and not payload.get("no_trade_reason"):
            payload["no_trade_reason"] = "Pi returned no_trade=true."
        try:
            decision = HourlyAgentDecision.model_validate(payload)
        except ValidationError as exc:
            return HourlyAgentDecision(
                summary="Pi JSON failed schema validation; defaulted to no_trade for safety.",
                no_trade_reason="Invalid decision JSON.",
                confidence=0.0,
                risk_notes=[str(exc)[:1000], json.dumps(payload)[:1000]],
            )
        if decision.memory_update:
            self.persistent_memory.append(f"{as_of.isoformat()}: {decision.memory_update}")
            self.persistent_memory = self.persistent_memory[-12:]
        return decision

    def _context_payload(
        self,
        *,
        as_of: datetime,
        portfolio: PortfolioState,
        prices: dict[str, float],
        candidate_intents: list[TradeIntent],
        market_digest: dict[str, Any],
    ) -> dict[str, Any]:
        positions = sorted(portfolio.positions.values(), key=lambda p: p.market_value, reverse=True)[: self.max_positions_to_show]
        candidates = candidate_intents[: self.max_candidates]
        visible_price_symbols = self._visible_price_symbols(
            prices=prices,
            positions=[p.ticker for p in positions],
            candidates=[intent.ticker for intent in candidates],
            market_digest=market_digest,
        )
        latest_prices = {k: round(prices[k], 4) for k in visible_price_symbols if k in prices}
        return {
            "simulated_time": as_of.isoformat(),
            "rules": [
                "Long-only equities/ETFs. No shorts, options, margin, leverage.",
                "Use only this timestamp-safe context; do not assume future prices/news.",
                "You may approve candidate intents, reject them, request closes, or no-trade.",
                "Risk validator can still reject anything unsafe.",
                "Return JSON only; no markdown/prose outside JSON.",
            ],
            "strategy_brief": self.strategy_brief,
            "agent_memory_tail": self.persistent_memory[-8:],
            "portfolio": {
                "cash": portfolio.cash,
                "equity": portfolio.equity,
                "gross_exposure": portfolio.gross_exposure,
                "positions": [p.model_dump() for p in positions],
            },
            "latest_prices": latest_prices,
            "latest_prices_coverage": {
                "shown": len(latest_prices),
                "available_internal": len(prices),
                "restricted": self.max_latest_prices_to_show is not None,
                "note": "If restricted, use market_digest ranked boards/symbol_context for opportunity discovery; replay still validates/fills against internal prices.",
            },
            "market_digest": market_digest,
            "candidate_intents": [intent.model_dump(mode="json") for intent in candidates],
            "output_schema": {
                "summary": "string",
                "approved_candidate_ids": ["intent ids to approve"],
                "rejected_candidate_ids": ["intent ids to reject"],
                "new_trade_intents": [
                    {
                        "ticker": "AAPL",
                        "side": "buy|sell|hold|no_trade",
                        "action": "buy|trim|close|hold|no_trade",
                        "target_weight": 0.05,
                        "horizon_days": 20,
                        "confidence": 0.65,
                        "expected_net_edge_bps": 100,
                        "thesis": "timestamp-safe reason",
                        "invalidators": ["..."],
                    }
                ],
                "close_tickers": ["tickers to close/trim to zero"],
                "no_trade_reason": "string or null",
                "confidence": 0.0,
                "memory_update": "short durable lesson, optional",
                "risk_notes": ["warnings"],
            },
        }

    def _visible_price_symbols(
        self,
        *,
        prices: dict[str, float],
        positions: list[str],
        candidates: list[str],
        market_digest: dict[str, Any],
    ) -> list[str]:
        if self.max_latest_prices_to_show is None:
            return [k for k, _ in sorted(prices.items())]
        selected: list[str] = []

        def add(symbol: str | None) -> None:
            if not symbol:
                return
            ticker = str(symbol).upper().strip()
            if ticker in prices and ticker not in selected:
                selected.append(ticker)

        for symbol in ["SPY", "QQQ", "IWM", "DIA", "VTI", "TLT", "GLD", "SLV", "USO", "XLE", "URA", "URNM"]:
            add(symbol)
        for symbol in positions:
            add(symbol)
        for symbol in candidates:
            add(symbol)
        for symbol in market_digest.get("visible_latest_price_symbols", []) or []:
            add(symbol)
            if len(selected) >= self.max_latest_prices_to_show:
                return selected[: self.max_latest_prices_to_show]
        for symbol, _ in sorted(prices.items()):
            add(symbol)
            if len(selected) >= self.max_latest_prices_to_show:
                break
        return selected[: self.max_latest_prices_to_show]

    def _write_context_file(self, as_of: datetime, payload: dict[str, Any]) -> Path:
        root = Path(self.context_file_dir)
        root.mkdir(parents=True, exist_ok=True)
        safe = as_of.strftime("%Y%m%d_%H%M")
        path = root / f"context_{safe}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path

    def _file_prompt(self, context_path: Path) -> str:
        return (
            "PROCESS THIS MARKET-HOUR CONTEXT NOW. Use the read tool to read this JSON file: "
            f"{context_path.as_posix()}. "
            "It contains portfolio, prices, market digest, candidate_intents, rules, and output_schema. "
            "Do not ask for more data. Candidate intents are pre-screened by timestamp-safe strategy code; "
            "approve them unless you have a concrete reason to reject from the file. "
            "Return exactly one valid JSON object matching output_schema; no markdown, no acknowledgement."
        )


def decision_to_intents(
    decision: HourlyAgentDecision,
    *,
    candidate_intents: list[TradeIntent],
    as_of: datetime,
    portfolio: PortfolioState,
    data_cutoff: datetime | None = None,
) -> list[TradeIntent]:
    cutoff = data_cutoff or as_of
    by_id = {intent.intent_id: intent for intent in candidate_intents}
    intents: list[TradeIntent] = []
    for intent_id in decision.approved_candidate_ids:
        if intent_id in by_id:
            intents.append(by_id[intent_id])
    for ticker in decision.close_tickers:
        ticker = ticker.upper().strip()
        if portfolio.position_quantity(ticker) <= 0:
            continue
        intents.append(
            TradeIntent(
                lab_id="hourly_pi_agent",
                originator="pi.gpt-5.5.hourly_agent",
                data_cutoff=cutoff,
                ticker=ticker,
                side="sell",
                action="close",
                target_weight=0.0,
                horizon_days=1,
                confidence=max(decision.confidence, 0.50),
                expected_net_edge_bps=9999.0,
                thesis=f"Pi agent requested close for {ticker}.",
                invalidators=[],
                metadata={"decision_id": decision.decision_id},
            )
        )
    for raw in decision.new_trade_intents:
        try:
            side = raw.get("side", "buy")
            action = raw.get("action", "buy")
            expected = raw.get("expected_net_edge_bps")
            if side == "sell" and action in {"trim", "close"}:
                expected = 9999.0
            intents.append(
                TradeIntent(
                    lab_id="hourly_pi_agent",
                    originator="pi.gpt-5.5.hourly_agent",
                    data_cutoff=cutoff,
                    ticker=str(raw["ticker"]).upper(),
                    side=side,
                    action=action,
                    target_weight=raw.get("target_weight"),
                    quantity=raw.get("quantity"),
                    horizon_days=int(raw.get("horizon_days", 20)),
                    confidence=float(raw.get("confidence", decision.confidence)),
                    expected_net_edge_bps=expected,
                    expected_gross_edge_bps=raw.get("expected_gross_edge_bps", expected),
                    thesis=str(raw.get("thesis", "Pi-generated hourly trade intent.")),
                    invalidators=list(raw.get("invalidators", [])),
                    metadata={"decision_id": decision.decision_id, "raw": raw},
                )
            )
        except Exception:
            continue
    return intents


def _extract_json(text: str) -> dict[str, Any] | None:
    from myaibot.agents.codex_cli import extract_first_json_object

    return extract_first_json_object(text)
