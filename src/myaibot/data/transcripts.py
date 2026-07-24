"""Earnings transcript ingestion contracts.

Coverage is source-dependent. The preferred free path is issuer-filed 8-K/EX-99
materials from SEC; vendor/scraped transcripts must be snapshot and license
reviewed before production use.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import new_id


class TranscriptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_id: str = Field(default_factory=lambda: new_id("transcript"))
    ticker: str
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    call_date: datetime | None = None
    source: str
    source_url: str | None = None
    published_at: datetime | None = None
    available_at: datetime
    title: str = ""
    text: str
    sha256: str | None = None
    license_notes: str = ""


def load_local_transcript_folder(path: str | Path, *, source: str = "local") -> pd.DataFrame:
    """Load `.txt` transcript snapshots named like `TICKER_YYYY_QN.txt` when present."""
    root = Path(path)
    rows = []
    for file in root.glob("*.txt"):
        parts = file.stem.split("_")
        ticker = parts[0].upper() if parts else file.stem.upper()
        year = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        quarter = int(parts[2].lstrip("Qq")) if len(parts) > 2 and parts[2].lstrip("Qq").isdigit() else None
        stat = file.stat()
        available_at = datetime.fromtimestamp(stat.st_mtime).astimezone()
        rows.append(
            {
                "ticker": ticker,
                "fiscal_year": year,
                "fiscal_quarter": quarter,
                "source": source,
                "source_url": str(file),
                "available_at": available_at,
                "title": file.stem,
                "text": file.read_text(encoding="utf-8", errors="replace"),
            }
        )
    return pd.DataFrame(rows)
