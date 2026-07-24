"""Raw data snapshot manifest contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import new_id, now_utc


class RawDataManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_id: str = Field(default_factory=lambda: new_id("manifest"))
    source: str
    snapshot_created_at: datetime = Field(default_factory=now_utc)
    allowed_until: datetime | None = None
    request_url: str | None = None
    request_params: dict[str, Any] = Field(default_factory=dict)
    fetched_at_utc: datetime
    source_timestamps: dict[str, datetime] = Field(default_factory=dict)
    http_headers: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None
    row_count: int | None = Field(default=None, ge=0)
    sha256: str
    parser_version: str | None = None
    availability_lag_notes: str = ""
    license_notes: str = ""
