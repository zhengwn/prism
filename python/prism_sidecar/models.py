"""Pydantic models — keep in sync with src/types/index.ts.

We use `alias_generator=to_camel` so the JSON output is camelCase (matching
the TypeScript types in `src/types/index.ts`). With `populate_by_name=True`,
Pydantic still accepts snake_case on input, which keeps the Python internals
readable.

The FastAPI endpoints that return these models MUST be declared with
`response_model_by_alias=True`, otherwise the JSON will fall back to the
snake_case field names.

v0.2a adds bilingual fields (`_en` / `_zh`) and source `config_json` so the
sidecar can store both the original (English) content and the LLM-distilled
Chinese version. The legacy `title` / `summary` / `key_points` / `tags`
fields are kept as compatibility shims (always equal to the zh version, or
falling back to en if distillation hasn't run yet) so the existing v0.1
frontend keeps rendering correctly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _CamelBase(BaseModel):
    """Auto-generate camelCase JSON aliases for snake_case fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# ----- Enums ---------------------------------------------------------------

class SourceKind(str, Enum):
    rss = "rss"
    youtube = "youtube"
    podcast = "podcast"
    blog = "blog"
    x = "x"
    pdf = "pdf"
    file = "file"
    # v0.2c+: B 站视频源。B 站字幕通常 1-2 万字，需要专用 distiller
    # prompt（章节切分 + 关键段选取 + CC/AI 字幕合并），见
    # ``distillers/bilibili_prompt.py``。
    bilibili = "bilibili"


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


class SyncJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    error = "error"
    # v0.2b+: user pressed Cancel. The job row keeps the partial
    # progress so a follow-up sync can pick up where this one
    # stopped; the value is distinct from "error" because the
    # user intentionally stopped it, not the pipeline itself.
    cancelled = "cancelled"


# ----- Source --------------------------------------------------------------

class Source(_CamelBase):
    id: str
    name: str
    kind: SourceKind
    url: str
    enabled: bool = True
    config_json: dict[str, Any] = Field(default_factory=dict)
    last_synced_at: Optional[datetime] = None
    last_error: Optional[str] = None
    item_count: int = 0
    created_at: Optional[datetime] = None


class SourceCreate(_CamelBase):
    name: str
    kind: SourceKind
    url: str
    enabled: bool = True
    config_json: dict[str, Any] = Field(default_factory=dict)


class SourcePatch(_CamelBase):
    """PATCH /api/sources/{id} — every field optional."""

    name: Optional[str] = None
    url: Optional[str] = None
    enabled: Optional[bool] = None
    config_json: Optional[dict[str, Any]] = None


# ----- Item ----------------------------------------------------------------

class KnowledgeItem(_CamelBase):
    """A single knowledge unit, distilled from a source.

    v0.2a adds explicit bilingual fields. `title` / `summary` / `key_points` /
    `tags` are convenience compatibility shims: they always equal the zh
    version (or fall back to en / [] when zh isn't available yet). New UI
    code should use the explicit `_en` / `_zh` fields.
    """

    id: str
    source_id: str
    source_name: str

    url: str

    # Bilingual content
    title_en: str
    title_zh: Optional[str] = None
    summary_en: Optional[str] = None
    summary_zh: Optional[str] = None
    key_points_zh: list[str] = Field(default_factory=list)
    tags_zh: list[str] = Field(default_factory=list)

    # Compat shims — populated by the model validator
    title: str = ""
    summary: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # Meta
    author: Optional[str] = None
    published_at: datetime
    fetched_at: datetime
    distilled_at: Optional[datetime] = None
    status: ItemStatus = ItemStatus.unread
    duration_sec: Optional[int] = None
    content_type: ContentType
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    # ---- validators -----------------------------------------------------

    @classmethod
    def _fallback(cls, zh: Optional[str], en: Optional[str], default: str = "") -> str:
        if zh:
            return zh
        if en:
            return en
        return default

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Populate compat shims from the bilingual fields.

        Title is required to be non-empty (schema guarantees title_en, so
        `title` is always populated).
        """
        # Title is required (en). Use zh if available.
        if not self.title:
            object.__setattr__(self, "title", self._fallback(self.title_zh, self.title_en))
        # Summary: prefer zh, then en.
        if self.summary is None:
            object.__setattr__(self, "summary", self._fallback(self.summary_zh, self.summary_en, default=""))
        # Lists: prefer zh, then empty.
        if not self.key_points:
            object.__setattr__(self, "key_points", list(self.key_points_zh))
        if not self.tags:
            object.__setattr__(self, "tags", list(self.tags_zh))


class ItemStatusPatch(BaseModel):
    status: ItemStatus


# ----- Sync ----------------------------------------------------------------

class SyncResult(_CamelBase):
    job_id: str
    source_id: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: SyncJobStatus = SyncJobStatus.pending
    sources_total: int = 0
    sources_done: int = 0
    items_new: int = 0
    items_distilled: int = 0
    error: Optional[str] = None


class SyncLogEntry(_CamelBase):
    id: int
    source_id: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    items_new: int = 0
    items_distilled: int = 0
    error: Optional[str] = None


# ----- Health --------------------------------------------------------------

class HealthInfo(_CamelBase):
    ok: bool = True
    version: str
    sources_count: int
    items_count: int
    distiller_configured: bool = False
    db_path: str
    uptime_sec: int
