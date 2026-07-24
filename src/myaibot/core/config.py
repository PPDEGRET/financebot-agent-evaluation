"""Configuration loading for the trading system."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import RiskLimits


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProtocolConfig(ConfigModel):
    train_end: date
    validation_start: date
    validation_end: date
    frozen_oos_start: date
    frozen_oos_end: date
    default_data_lag_days: int = Field(default=1, ge=0)


class PortfolioConfig(ConfigModel):
    initial_cash: float = Field(default=100_000.0, gt=0)
    allow_fractional_shares: bool = False
    execution_timing: str = "next_close"
    benchmark_symbols: list[str] = Field(default_factory=lambda: ["SPY"])


class AgentConfig(ConfigModel):
    primary_model: str = "gpt-5.5"
    reasoning_efforts_to_compare: list[str] = Field(default_factory=lambda: ["medium", "high", "xhigh"])
    default_reasoning_effort: str = "high"
    require_trade_memo_for_weight_above: float = 0.02
    require_skeptic_for_weight_above: float = 0.03


class GlobalConfig(ConfigModel):
    project: dict[str, Any] = Field(default_factory=dict)
    protocol: ProtocolConfig
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    labs: dict[str, dict[str, Any]] = Field(default_factory=dict)


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in config file: {path}")
    return data


def load_global_config(path: str | Path = "configs/global.yaml") -> GlobalConfig:
    return GlobalConfig.model_validate(load_yaml(path))
