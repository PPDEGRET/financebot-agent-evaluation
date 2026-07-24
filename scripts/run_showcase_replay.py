#!/usr/bin/env python3
"""Run the offline deterministic FINANCEBOT sample tournament."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("FINANCEBOT_SHOWCASE_MODE", "1")

from myaibot.agents.hourly_agent import HourlyTradingAgent
from myaibot.backtest.hourly_replay import HourlyReplayEngine, HourlyReplayResult
from myaibot.backtest.metrics import benchmark_return, equity_curve_from_snapshots, performance_summary
from myaibot.core.models import RiskLimits
from myaibot.core.showcase import showcase_mode_enabled
from myaibot.execution.ledger import PaperLedger
from myaibot.labs.atlas_regime import AtlasRegimeConfig, AtlasRegimeLab
from myaibot.labs.static_momentum import StaticMomentumConfig, StaticMomentumLab
from myaibot.portfolio.optimizer import EnsembleConfig, EnsemblePortfolioManager

DATA_DIR = ROOT / "data" / "synthetic"
CONFIG_PATH = ROOT / "configs" / "showcase.yaml"
DEFAULT_OUTPUT = ROOT / "artifacts" / "sample-replay.json"
SCHEDULE_LABELS = {
    "daily_open": "Open only",
    "open_close": "Open + close",
    "three_times_day": "Three times daily",
    "every_hour": "Hourly",
}


class CountingPolicyAgent:
    """Count deterministic policy reviews without changing the agent interface."""

    def __init__(self) -> None:
        self.calls = 0
        self.inner = HourlyTradingAgent(
            mode="policy",
            policy_style="approve_all",
            strategy_brief="Synthetic demonstration: approve deterministic momentum candidates; no model runtime.",
        )

    def decide(self, **kwargs):
        self.calls += 1
        return self.inner.decide(**kwargs)


def _load_config() -> dict[str, Any]:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    showcase = cfg["showcase"]
    if showcase["mode"] != "offline" or any(
        showcase[key] for key in ("external_model_calls", "network_data_downloads", "broker_connections")
    ):
        raise RuntimeError("Showcase config must remain offline with every external capability disabled.")
    if cfg["execution"]["mode"] != "paper_ledger" or cfg["execution"]["fill_timing"] != "next_synthetic_bar":
        raise RuntimeError("Showcase execution must remain next-bar paper-ledger only.")
    if cfg["strategy"]["lab"] != "static_momentum":
        raise RuntimeError("The deterministic showcase supports only the declared static_momentum lab.")
    return cfg


def _load_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = pd.read_csv(DATA_DIR / "daily_prices.csv", index_col=0, parse_dates=True)
    volume = pd.read_csv(DATA_DIR / "daily_volume.csv", index_col=0, parse_dates=True)
    hourly = pd.read_csv(DATA_DIR / "hourly_prices.csv", index_col=0, parse_dates=True)
    return daily, volume, hourly


def _verify_dataset_manifest() -> str:
    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, details in manifest["files"].items():
        digest = hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest()
        if digest != details["sha256"]:
            raise RuntimeError(f"Synthetic dataset hash mismatch: {name}")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 8)


def run_variant(
    schedule: str,
    *,
    cfg: dict[str, Any],
    daily: pd.DataFrame,
    volume: pd.DataFrame,
    hourly: pd.DataFrame,
) -> tuple[dict[str, Any], HourlyReplayResult]:
    replay_cfg = cfg["replay"]
    risk_cfg = cfg["risk"]
    strategy_cfg = cfg["strategy"]
    execution_cfg = cfg["execution"]

    limits = RiskLimits(
        allow_shorts=bool(risk_cfg["allow_short"]),
        allow_margin=bool(risk_cfg["allow_margin"]),
        max_position_weight=float(risk_cfg["max_position_weight"]),
        max_order_notional_weight=float(risk_cfg["max_order_notional_pct"]),
        max_sector_weight=float(risk_cfg["max_sector_weight"]),
        max_positions=int(risk_cfg["max_positions"]),
        cash_buffer_weight=float(risk_cfg["min_cash_buffer_pct"]),
        min_price=float(risk_cfg["min_price"]),
        min_adv_dollars=float(risk_cfg["min_adv_dollars"]),
        min_expected_net_edge_bps=0.0,
        cost_bps_per_side=float(execution_cfg["transaction_cost_bps"]),
        slippage_bps=float(execution_cfg["slippage_bps"]),
    )
    lab = StaticMomentumLab(
        StaticMomentumConfig(
            lookback_bars=int(strategy_cfg["lookback_bars"]),
            top_n=int(strategy_cfg["top_n"]),
            min_return=-1.0,
            exclude_symbols=(str(strategy_cfg["regime_benchmark"]),),
            freeze_selection=False,
        )
    )
    manager = EnsemblePortfolioManager(
        EnsembleConfig(
            lab_weights={"static_momentum": 1.0},
            max_names=int(strategy_cfg["top_n"]),
            max_weight_per_name=float(strategy_cfg["max_name_weight"]),
            min_confidence=0.05,
            rebalance_threshold_weight=float(strategy_cfg["rebalance_threshold"]),
            close_unranked_positions=True,
            originator="showcase.deterministic_policy",
        )
    )
    agent = CountingPolicyAgent()
    start_ts = pd.Timestamp(replay_cfg["start"])
    historical = daily[daily.index < start_ts]
    adv = (historical.tail(20) * volume.reindex(historical.index).tail(20)).mean().dropna().to_dict()
    engine = HourlyReplayEngine(
        daily_prices=daily,
        hourly_prices=hourly,
        daily_volume=volume,
        labs=[lab],
        portfolio_manager=manager,
        agent=agent,  # type: ignore[arg-type]
        ledger=PaperLedger(initial_cash=float(replay_cfg["initial_cash"])),
        limits=limits,
        atlas_lab=AtlasRegimeLab(
            AtlasRegimeConfig(
                benchmark=str(strategy_cfg["regime_benchmark"]),
                fast_bars=int(strategy_cfg["regime_fast_window"]),
                slow_bars=int(strategy_cfg["regime_slow_window"]),
            )
        ),
        adv_dollars={str(k): float(v) for k, v in adv.items()},
        sectors={
            "SYN_ORBIT": "synthetic_growth",
            "SYN_CEDAR": "synthetic_quality",
            "SYN_HARBOR": "synthetic_defensive",
            "SYN_RIDGE": "synthetic_cyclical",
            "SYN_BENCH": "synthetic_benchmark",
        },
        summon_on_candidates=False,
        write_every_steps=0,
    )
    result = engine.run(
        start=f"{replay_cfg['start']} 09:30",
        end=f"{replay_cfg['end']} 15:30",
        summon_policy=schedule,  # type: ignore[arg-type]
        continue_pi_session=False,
    )
    summary = performance_summary(
        equity_curve_from_snapshots(result.snapshots),
        initial_cash=float(replay_cfg["initial_cash"]),
    )
    benchmark = benchmark_return(
        hourly,
        str(strategy_cfg["regime_benchmark"]),
        f"{replay_cfg['start']} 09:30",
        f"{replay_cfg['end']} 15:30",
    )
    approved_validations = sum(1 for validation in result.validations if validation.approved)
    rejected_validations = len(result.validations) - approved_validations
    row = {
        "id": schedule,
        "label": SCHEDULE_LABELS[schedule],
        "review_schedule": {
            "daily_open": "09:30 synthetic ET",
            "open_close": "09:30 and 15:30 synthetic ET",
            "three_times_day": "09:30, 12:30, and 15:30 synthetic ET",
            "every_hour": "Every synthetic market-hour bar",
        }[schedule],
        "simulated_return": _round(summary["total_return"]),
        "synthetic_benchmark_return": _round(benchmark),
        "max_drawdown": _round(summary["max_drawdown"]),
        "reported_sharpe": _round(summary["sharpe"]),
        "final_equity": _round(summary["final_equity"]),
        "fills": len(result.fills),
        "policy_calls": agent.calls,
        "candidate_intents": len(result.candidate_intents),
        "approved_validations": approved_validations,
        "rejected_validations": rejected_validations,
    }
    return row, result


def _position_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ticker": ticker,
            "quantity": int(position["quantity"]),
            "avg_cost": _round(position["avg_cost"]),
            "last_price": _round(position["last_price"]),
            "market_value": _round(float(position["quantity"]) * float(position["last_price"])),
        }
        for ticker, position in sorted(snapshot.get("positions", {}).items())
    ]


def _ledger_view(snapshot: dict[str, Any] | None, *, initial_cash: float, fill_count: int) -> dict[str, Any]:
    if snapshot is None:
        return {
            "cash": _round(initial_cash),
            "market_value": 0.0,
            "equity": _round(initial_cash),
            "fill_count": fill_count,
            "positions": [],
        }
    return {
        "cash": _round(snapshot["cash"]),
        "market_value": _round(snapshot["market_value"]),
        "equity": _round(snapshot["equity"]),
        "fill_count": fill_count,
        "positions": _position_rows(snapshot),
    }


def _build_decision_trace(result: HourlyReplayResult, *, initial_cash: float) -> dict[str, Any]:
    fill_decision_times = {event.decision_time for event in result.events if event.kind == "fill"}
    decision_event = next(
        event
        for event in result.events
        if event.kind == "agent_decision" and event.decision_time in fill_decision_times
    )
    decision_time = decision_event.decision_time
    data_cutoff = decision_event.data_cutoff
    if decision_time is None or data_cutoff is None:
        raise RuntimeError("Decision trace is missing timestamp boundaries.")

    approved_ids = list(decision_event.payload.get("approved_candidate_ids", []))
    candidates_by_id = {intent.intent_id: intent for intent in result.candidate_intents}
    validations_by_id = {validation.intent_id: validation for validation in result.validations}
    fill_events_by_intent = {
        str(event.payload.get("intent_id")): event
        for event in result.events
        if event.kind == "fill" and event.decision_time == decision_time
    }
    selected_candidates = [candidates_by_id[intent_id] for intent_id in approved_ids]
    selected_validations = [validations_by_id[intent_id] for intent_id in approved_ids]
    selected_fills = [fill_events_by_intent[intent_id] for intent_id in approved_ids]
    execution_time = selected_fills[0].execution_time
    if execution_time is None or any(event.execution_time != execution_time for event in selected_fills):
        raise RuntimeError("Decision trace fills do not share one deterministic execution bar.")

    decision_iso = decision_time.isoformat()
    current_snapshot = next(snapshot for snapshot in result.snapshots if snapshot["decision_time"] == decision_iso)
    previous_snapshots = [
        snapshot
        for snapshot in result.snapshots
        if pd.Timestamp(snapshot["execution_time"]) <= pd.Timestamp(data_cutoff)
    ]
    previous_snapshot = previous_snapshots[-1] if previous_snapshots else None
    fills_before = sum(
        event.kind == "fill" and event.execution_time is not None and event.execution_time <= data_cutoff
        for event in result.events
    )
    fills_after = sum(
        event.kind == "fill" and event.execution_time is not None and event.execution_time <= execution_time
        for event in result.events
    )
    ledger_before = _ledger_view(previous_snapshot, initial_cash=initial_cash, fill_count=fills_before)
    ledger_after = _ledger_view(current_snapshot, initial_cash=initial_cash, fill_count=fills_after)

    candidates = [
        {
            "ticker": intent.ticker,
            "side": intent.side,
            "action": intent.action,
            "target_weight": _round(intent.target_weight),
            "confidence": _round(intent.confidence),
            "expected_net_edge_bps": _round(intent.expected_net_edge_bps),
            "thesis": intent.thesis,
        }
        for intent in selected_candidates
    ]
    validations = [
        {
            "ticker": validation.order.ticker if validation.order else candidates[index]["ticker"],
            "approved": validation.approved,
            "quantity": validation.order.quantity if validation.order else None,
            "visible_price": _round(validation.order.expected_price) if validation.order else None,
            "estimated_fees": _round(validation.order.estimated_fees) if validation.order else None,
            "estimated_slippage": _round(validation.order.estimated_slippage) if validation.order else None,
            "issues": [
                {"severity": issue.severity, "code": issue.code, "message": issue.message}
                for issue in validation.issues
            ],
        }
        for index, validation in enumerate(selected_validations)
    ]
    fills = [
        {
            "ticker": str(event.payload["ticker"]),
            "side": str(event.payload["side"]),
            "quantity": int(event.payload["quantity"]),
            "execution_price": _round(event.payload["price"]),
            "commission": _round(event.payload["commission"]),
            "slippage_bps": _round(event.payload["slippage_bps"]),
            "execution_time": execution_time.isoformat(),
            "venue": str(event.payload["venue"]),
        }
        for event in selected_fills
    ]
    strict_ordering = data_cutoff < decision_time < execution_time
    if not strict_ordering or not all(validation["approved"] for validation in validations):
        raise RuntimeError("Decision trace failed timestamp or validation invariants.")

    trace: dict[str, Any] = {
        "label": "Synthetic demonstration",
        "trace_variant": "open_close",
        "review_schedule": "09:30 and 15:30 synthetic ET",
        "policy": "deterministic approve_all",
        "external_model_calls": 0,
        "data_cutoff": data_cutoff.isoformat(),
        "decision_time": decision_time.isoformat(),
        "execution_time": execution_time.isoformat(),
        "strict_timestamp_ordering": strict_ordering,
        "context": {
            "available_through": data_cutoff.isoformat(),
            "regime_multiplier": _round(current_snapshot["regime_multiplier"]),
            "candidate_count": len(candidates),
            "visible_prices": {validation["ticker"]: validation["visible_price"] for validation in validations},
        },
        "candidates": candidates,
        "decision": {
            "summary": str(decision_event.payload["summary"]),
            "confidence": _round(decision_event.payload["confidence"]),
            "approved_tickers": [candidate["ticker"] for candidate in candidates],
            "rejected_candidates": len(decision_event.payload.get("rejected_candidate_ids", [])),
        },
        "validations": validations,
        "fills": fills,
        "ledger_before": ledger_before,
        "ledger_after": ledger_after,
        "ledger_delta": {
            "cash": _round(float(ledger_after["cash"]) - float(ledger_before["cash"])),
            "market_value": _round(float(ledger_after["market_value"]) - float(ledger_before["market_value"])),
            "equity": _round(float(ledger_after["equity"]) - float(ledger_before["equity"])),
            "fills": int(ledger_after["fill_count"]) - int(ledger_before["fill_count"]),
        },
        "stages": [
            {"index": 1, "name": "Bound context", "status": "available_at enforced"},
            {"index": 2, "name": "Generate candidates", "status": f"{len(candidates)} deterministic intents"},
            {"index": 3, "name": "Policy review", "status": "structured approval; no model call"},
            {"index": 4, "name": "Risk validation", "status": f"{len(validations)} approved by real validator"},
            {"index": 5, "name": "Next-bar fills", "status": f"{len(fills)} paper fills with costs"},
            {"index": 6, "name": "Ledger snapshot", "status": "cash + positions reconciled"},
        ],
        "caveat": "This trace uses generated prices and a deterministic policy. It demonstrates control flow, not historical model behavior or financial performance.",
    }
    canonical = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    trace["trace_sha256"] = hashlib.sha256(canonical).hexdigest()
    return trace


def build_report() -> dict[str, Any]:
    if not showcase_mode_enabled():
        raise RuntimeError("Refusing to run portfolio demo with showcase mode disabled.")
    manifest_digest = _verify_dataset_manifest()
    cfg = _load_config()
    daily, volume, hourly = _load_frames()
    schedules = list(cfg["replay"]["review_schedules"])
    runs = [run_variant(schedule, cfg=cfg, daily=daily, volume=volume, hourly=hourly) for schedule in schedules]
    variants = [row for row, _ in runs]
    open_close_result = next(result for row, result in runs if row["id"] == "open_close")
    decision_trace = _build_decision_trace(open_close_result, initial_cash=float(cfg["replay"]["initial_cash"]))
    return {
        "schema_version": 1,
        "label": str(cfg["showcase"]["label"]),
        "mode": "offline_deterministic_policy_and_paper_ledger",
        "historical_result_reproduction": False,
        "showcase_config_sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
        "dataset_manifest_sha256": manifest_digest,
        "window": {"start": cfg["replay"]["start"], "end": cfg["replay"]["end"]},
        "execution_assumption": "Decisions see only the previous bar; approved orders fill on the next synthetic bar with configured costs and slippage.",
        "external_model_calls": 0,
        "broker_connections": 0,
        "variants": variants,
        "decision_trace": decision_trace,
        "caveat": "This generated fixture exercises replay, invocation scheduling, validation, and accounting. Its performance has no financial meaning.",
    }


def _serialized(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Compare a fresh replay with the committed deterministic artifact.")
    args = parser.parse_args()

    report = build_report()
    rendered = _serialized(report)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"Replay check failed: regenerate {args.output}")
        print("Showcase replay check passed: deterministic artifact matches.")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}")
    for row in report["variants"]:
        print(
            f"{row['label']:<18} calls={row['policy_calls']:>3} fills={row['fills']:>3} "
            f"return={row['simulated_return']:+.2%} drawdown={row['max_drawdown']:+.2%}"
        )
    print("Synthetic demonstration only; no network, model provider, broker, or live capital.")


if __name__ == "__main__":
    main()
