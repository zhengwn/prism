"""Helpers shared by the store submodules (row mappers, id/ISO utils).

Private to the `prism_sidecar.store` package — external callers go
through the package façade (`from prism_sidecar import store`).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from prism_sidecar.models import ContentType, ItemStatus, KnowledgeItem

# User tags are joined into item rows as a single group_concat string with
# this delimiter — a control char (unit separator) that add_item_tag rejects
# in tag text, so it can never collide with a real tag.
_TAG_SEP = "\x1f"

# SELECT fragment for the per-row user-tags list, shared by list_items /
# get_item / semantic_search. Correlated subquery, indexed by item_tags'
# (item_id, tag) PK.
_USER_TAGS_SELECT = (
    "(SELECT group_concat(tag, char(31)) FROM item_tags WHERE item_id = i.id) "
    "AS user_tags"
)


def _split_user_tags(joined: Optional[str]) -> list[str]:
    """Turn a group_concat(tag, _TAG_SEP) string back into a tag list."""
    if not joined:
        return []
    return [t for t in joined.split(_TAG_SEP) if t]


def _row_to_item(
    row: tuple,
    source_name: str | None = None,
    user_tags: Optional[str] = None,
) -> KnowledgeItem:
    (
        iid,
        sid,
        url,
        title_en,
        title_zh,
        summary_en,
        summary_zh,
        key_points_zh_json,
        tags_zh_json,
        author,
        published_at,
        fetched_at,
        distilled_at,
        status,
        content_type,
        duration_sec,
        metadata_json,
    ) = row
    return KnowledgeItem(
        id=iid,
        source_id=sid,
        source_name=source_name or sid,
        url=url,
        title_en=title_en,
        title_zh=title_zh,
        summary_en=summary_en,
        summary_zh=summary_zh,
        key_points_zh=json.loads(key_points_zh_json) if key_points_zh_json else [],
        tags_zh=json.loads(tags_zh_json) if tags_zh_json else [],
        user_tags=_split_user_tags(user_tags),
        author=author,
        published_at=_parse_iso(published_at) or datetime.now(timezone.utc),
        fetched_at=_parse_iso(fetched_at) or datetime.now(timezone.utc),
        distilled_at=_parse_iso(distilled_at),
        status=ItemStatus(status),
        content_type=ContentType(content_type),
        duration_sec=int(duration_sec) if duration_sec is not None else None,
        metadata_json=json.loads(metadata_json) if metadata_json else {},
    )


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            # Python 3.11+ supports the "Z" suffix in fromisoformat.
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
