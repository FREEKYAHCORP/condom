from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RawResponseIn(BaseModel):
    session_id: str
    response_id: str
    url: str
    body: Any
    captured_at: str | None = None


class ItemsIn(BaseModel):
    session_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class EventsIn(BaseModel):
    session_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)


class FeedM3RequestIn(BaseModel):
    session_id: str
    batch_size: int = Field(default=50, ge=1, le=64)
    max_batches: int = Field(default=1, ge=1, le=32)
