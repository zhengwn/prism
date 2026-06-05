"""Pydantic models — keep in sync with src/types/index.ts.

We use `alias_generator=to_camel` so the JSON output is camelCase (matching
the TypeScript types in `src/types/index.ts`). With `populate_by_name=True`,
Pydantic still accepts snake_case on input, which keeps the Python internals
readable.

The FastAPI endpoints that return these models MUST be declared with
`response_model_by_alias=True`, otherwise the JSON will fall back to the
snake_case field names.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _CamelBase(BaseModel):
    """Auto-generate camelCase JSON aliases for snake_case fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class SourceKind(str, Enum):
    rss = "rss"
    youtube = "youtube"
    podcast = "podcast"
    blog = "blog"
    x = "x"
    pdf = "pdf"
    file = "file"


class Source(_CamelBase):
    id: str
    name: str
    kind: SourceKind
    url: str
    enabled: bool = True
    last_synced_at: Optional[datetime] = None
    item_count: int = 0


class ItemStatus(str, Enum):
    unread = "unread"
    read = "read"
    archived = "archived"
    starred = "starred"


class ContentType(str, Enum):
    video = "video"
    audio = "audio"
    article = "article"
    paper = "paper"
    post = "post"


class KnowledgeItem(_CamelBase):
    id: str
    source_id: str
    source_name: str
    title: str
    url: str
    author: Optional[str] = None
    published_at: datetime
    fetched_at: datetime
    status: ItemStatus = ItemStatus.unread
    summary: Optional[str] = None
    key_points: list[str] = []
    tags: list[str] = []
    duration_sec: Optional[int] = None
    content_type: ContentType


class SourceCreate(BaseModel):
    name: str
    kind: SourceKind
    url: str
    enabled: bool = True


class HealthInfo(_CamelBase):
    ok: bool = True
    version: str
    sources_count: int
    items_count: int
    uptime_sec: int
