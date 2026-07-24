"""Fail-closed capability guard for the public showcase package."""

from __future__ import annotations

import os

SHOWCASE_MODE_ENV = "FINANCEBOT_SHOWCASE_MODE"
_FALSE_VALUES = {"0", "false", "no", "off"}


class ShowcaseSafetyError(RuntimeError):
    """Raised when an external or live capability is attempted in showcase mode."""


def showcase_mode_enabled() -> bool:
    """Return whether fail-closed showcase mode is active.

    The public package defaults to showcase mode even when the environment
    variable is absent. This avoids turning a missing configuration value into
    permission for an external side effect.
    """

    raw = os.getenv(SHOWCASE_MODE_ENV, "1").strip().lower()
    return raw not in _FALSE_VALUES


def require_external_runtime(capability: str) -> None:
    """Reject a side-effecting/external capability while showcase mode is on."""

    if showcase_mode_enabled():
        raise ShowcaseSafetyError(
            f"{capability} is unavailable in FINANCEBOT showcase mode. "
            "The portfolio demo is offline, deterministic, and paper-ledger only."
        )
