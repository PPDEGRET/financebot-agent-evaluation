import hashlib
import json
from pathlib import Path

import pytest

from myaibot.agents.codex_cli import CodexCliRunner
from myaibot.agents.pi_cli import PiCliRunner
from myaibot.core.showcase import ShowcaseSafetyError, showcase_mode_enabled
from myaibot.execution.ibkr_adapter import IbAsyncBrokerAdapter, IbkrConnectionConfig

ROOT = Path(__file__).resolve().parents[2]


def test_showcase_mode_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("FINANCEBOT_SHOWCASE_MODE", raising=False)
    assert showcase_mode_enabled() is True


def test_broker_methods_are_inaccessible_in_showcase_mode(monkeypatch):
    monkeypatch.setenv("FINANCEBOT_SHOWCASE_MODE", "1")
    adapter = IbAsyncBrokerAdapter(config=IbkrConnectionConfig(transmit_orders=True))
    for action in (adapter.connect, adapter.disconnect, adapter.reconcile, adapter.open_order_ids):
        with pytest.raises(ShowcaseSafetyError):
            action()


def test_external_model_runtimes_are_inaccessible_in_showcase_mode(monkeypatch):
    monkeypatch.setenv("FINANCEBOT_SHOWCASE_MODE", "1")
    with pytest.raises(ShowcaseSafetyError):
        PiCliRunner().run_text("must not run")
    with pytest.raises(ShowcaseSafetyError):
        CodexCliRunner().run_text("must not run")


def test_synthetic_dataset_manifest_matches_files():
    root = ROOT / "data" / "synthetic"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["label"] == "Synthetic demonstration"
    assert "no observed market data" in manifest["origin"]
    for name, details in manifest["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == details["sha256"]


def test_committed_replay_is_offline_and_compares_four_schedules():
    report = json.loads((ROOT / "artifacts" / "sample-replay.json").read_text(encoding="utf-8"))
    assert report["label"] == "Synthetic demonstration"
    assert report["external_model_calls"] == 0
    assert report["broker_connections"] == 0
    assert report["showcase_config_sha256"] == hashlib.sha256((ROOT / "configs" / "showcase.yaml").read_bytes()).hexdigest()
    assert [row["id"] for row in report["variants"]] == [
        "daily_open",
        "open_close",
        "three_times_day",
        "every_hour",
    ]
    assert [row["policy_calls"] for row in report["variants"]] == [15, 29, 44, 104]


def test_historical_highlight_carries_every_required_caveat():
    evidence = json.loads((ROOT / "evidence" / "tournament-results.json").read_text(encoding="utf-8"))
    rows = evidence["historical_evidence"]["comparison_window"]["variants"]
    highlighted = next(row for row in rows if row["id"] == "open_close")
    assert highlighted["simulated_return"] == pytest.approx(0.2634153532)
    assert highlighted["max_drawdown"] == pytest.approx(-0.2165398536)
    assert highlighted["reported_sharpe"] == pytest.approx(0.4666382563)
    assert highlighted["benchmark"] == pytest.approx(0.1553662349)
    caveats = " ".join(evidence["required_caveats"]).lower()
    for phrase in (
        "current-listing-universe bias",
        "paper execution assumptions",
        "january selection and repeated-testing risk",
        "short historical regime",
        "non-deterministic model decisions",
        "no live capital",
    ):
        assert phrase in caveats
