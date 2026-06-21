from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Item:
    item_id: str
    source: str
    session_id: str
    batch_id: str | None = None
    original_rank: int | None = None
    author_handle: str | None = None
    author_name: str | None = None
    author_bio: str | None = None
    text: str | None = None
    quoted_text: str | None = None
    thread_context: str | None = None
    media_desc: str | None = None
    link_url: str | None = None
    link_title: str | None = None
    link_excerpt: str | None = None
    engagement: dict[str, Any] | None = None
    raw_json: dict[str, Any] | None = None

    def asdict(self) -> dict[str, Any]:
        return asdict(self)
