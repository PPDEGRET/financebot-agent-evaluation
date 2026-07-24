#!/usr/bin/env python3
"""Write or verify the frozen prospective paper-protocol digests."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "protocol" / "frozen-paper-v1.json"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configuration_digest(protocol: dict) -> str:
    canonical = copy.deepcopy(protocol)
    canonical.pop("configuration_sha256", None)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def expected_protocol() -> dict:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    brief_path = ROOT / protocol["brief"]["path"]
    protocol["brief"]["sha256"] = file_digest(brief_path)
    protocol["configuration_sha256"] = configuration_digest(protocol)
    return protocol


def render(protocol: dict) -> str:
    return json.dumps(protocol, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update both recorded digests.")
    parser.add_argument("--check", action="store_true", help="Fail if the protocol or brief changed after freezing.")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")

    current = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    expected = expected_protocol()
    if args.write:
        PROTOCOL_PATH.write_text(render(expected), encoding="utf-8")
        print(f"Frozen protocol {expected['protocol_id']} at {expected['configuration_sha256']}")
        return

    if current != expected:
        raise SystemExit("Frozen protocol check failed: configuration or brief digest changed.")
    print(f"Frozen protocol check passed: {current['configuration_sha256']}")


if __name__ == "__main__":
    main()
