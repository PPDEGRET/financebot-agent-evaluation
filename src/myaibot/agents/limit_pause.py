"""Usage-limit pause/heartbeat helpers for Pi/Codex runs."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class UsageLimitInfo:
    detected: bool
    resets_at_epoch: int | None = None
    resets_in_seconds: int | None = None
    raw: str = ""


def parse_usage_limit_error(text: str) -> UsageLimitInfo:
    if "usage_limit_reached" not in text and "Primary-Reset" not in text and "resets_at" not in text:
        return UsageLimitInfo(detected=False, raw=text)
    resets_at = _first_int(text, r'"resets_at"\s*:\s*(\d+)') or _first_int(text, r'"X-Codex-Primary-Reset-At"\s*:\s*"?(\d+)')
    resets_in = _first_int(text, r'"resets_in_seconds"\s*:\s*(\d+)') or _first_int(text, r'"X-Codex-Primary-Reset-After-Seconds"\s*:\s*"?(\d+)')
    return UsageLimitInfo(detected=True, resets_at_epoch=resets_at, resets_in_seconds=resets_in, raw=text)


def _first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


class UsageLimitPause:
    """Heartbeat pause that sleeps until Codex/Pi usage reset.

    This intentionally does not crash the tournament. It writes heartbeat records
    so the run can be monitored and audited.
    """

    def __init__(self, heartbeat_path: str | Path = "research/experiments/tournament/heartbeat.jsonl", poll_seconds: int = 60) -> None:
        self.heartbeat_path = Path(heartbeat_path)
        self.poll_seconds = poll_seconds

    def wait_if_needed(self, error_text: str, *, context: dict | None = None) -> bool:
        info = parse_usage_limit_error(error_text)
        if not info.detected:
            return False
        now = int(time.time())
        wait_until = info.resets_at_epoch or (now + (info.resets_in_seconds or self.poll_seconds))
        self._write(
            {
                "event": "usage_limit_pause_start",
                "at": datetime.now(UTC).isoformat(),
                "wait_until_epoch": wait_until,
                "wait_seconds_initial": max(wait_until - now, 0),
                "context": context or {},
            }
        )
        while True:
            remaining = wait_until - int(time.time())
            if remaining <= 0:
                break
            self._write(
                {
                    "event": "usage_limit_pause_heartbeat",
                    "at": datetime.now(UTC).isoformat(),
                    "remaining_seconds": remaining,
                    "context": context or {},
                }
            )
            time.sleep(min(self.poll_seconds, max(remaining, 1)))
        self._write(
            {
                "event": "usage_limit_pause_end",
                "at": datetime.now(UTC).isoformat(),
                "context": context or {},
            }
        )
        return True

    def _write(self, payload: dict) -> None:
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        with self.heartbeat_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
