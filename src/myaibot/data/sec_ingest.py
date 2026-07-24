"""SEC EDGAR ingestion helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from myaibot.data.manifest import RawDataManifest

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def sec_user_agent(company: str = "myaibot", email: str = "research@example.com") -> str:
    return f"{company} {email}"


def fetch_sec_json(url: str, *, user_agent: str, out_path: str | Path | None = None) -> tuple[dict, RawDataManifest]:
    req = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    fetched_at = datetime.now(UTC)
    with urlopen(req, timeout=30) as response:  # nosec - URL passed by caller/SEC constants
        raw = response.read()
        headers = dict(response.headers.items())
    sha = hashlib.sha256(raw).hexdigest()
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = RawDataManifest(
        source="sec_edgar",
        request_url=url,
        fetched_at_utc=fetched_at,
        http_headers=headers,
        content_type=headers.get("Content-Type"),
        row_count=None,
        sha256=sha,
        availability_lag_notes="SEC acceptanceDateTime is preferred when parsing filings; fetched_at is observation time.",
    )
    return json.loads(raw.decode("utf-8")), manifest


def fetch_company_tickers(*, user_agent: str, out_dir: str | Path = "data/raw/sec") -> pd.DataFrame:
    data, manifest = fetch_sec_json(SEC_COMPANY_TICKERS_URL, user_agent=user_agent, out_path=Path(out_dir) / "company_tickers.json")
    df = pd.DataFrame(data.values()).rename(columns={"ticker": "symbol", "title": "company_name", "cik_str": "cik"})
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    manifest.row_count = len(df)  # type: ignore[misc]
    Path(out_dir, "company_tickers.manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return df[["symbol", "cik", "company_name"]]


def fetch_company_submissions(cik: int | str, *, user_agent: str, out_dir: str | Path = "data/raw/sec/submissions") -> tuple[pd.DataFrame, RawDataManifest]:
    cik10 = str(cik).zfill(10)
    url = SEC_SUBMISSIONS_URL.format(cik10=cik10)
    path = Path(out_dir) / f"CIK{cik10}.json"
    data, manifest = fetch_sec_json(url, user_agent=user_agent, out_path=path)
    recent = data.get("filings", {}).get("recent", {})
    df = pd.DataFrame(recent)
    if not df.empty and "acceptanceDateTime" in df.columns:
        df["available_at"] = pd.to_datetime(df["acceptanceDateTime"], errors="coerce", utc=True)
    manifest.row_count = len(df)  # type: ignore[misc]
    path.with_suffix(".manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return df, manifest
