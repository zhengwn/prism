"""SQLite-backed data layer (replaces the v0.1 in-memory store).

All functions are async because aiosqlite returns awaitables. The store
shares the single aiosqlite connection opened by `db.init_db()`.

Package layout (split from the original single 1200-line store.py; the
public surface is unchanged — callers keep doing
``from prism_sidecar import store`` + ``store.fn(...)``, or importing
names directly from ``prism_sidecar.store``):

    _shared.py   row mappers + id/ISO helpers shared across submodules
    health.py    health_snapshot (GET /health)
    sources.py   sources CRUD, sync-status columns, first-run seeding
    items.py     items list/get (FTS5-aware), insert, distill/status updates
    tags.py      user tags (v0.5)
    vectors.py   sqlite-vec embeddings + semantic search (v0.5)
    webhooks.py  webhook rows + delivery bookkeeping (v0.3)
    jobs.py      sync_jobs + sync_log history
    meta.py      _meta key/value flags
"""

from __future__ import annotations

from prism_sidecar.store.health import health_snapshot
from prism_sidecar.store.items import (
    get_item,
    get_item_content,
    insert_item_from_raw,
    item_exists_by_url,
    list_items,
    update_item_distilled,
    update_item_status,
)
from prism_sidecar.store.jobs import (
    create_job,
    fail_orphan_running_jobs,
    finish_job,
    get_job,
    is_any_job_running,
    list_recent_jobs,
    list_sync_history,
    update_job_progress,
    write_sync_log,
)
from prism_sidecar.store.meta import get_meta, has_meta, set_meta
from prism_sidecar.store.sources import (
    create_source,
    delete_source,
    ensure_default_sources,
    get_source,
    list_sources,
    mark_source_error,
    mark_source_synced,
    patch_source,
)
from prism_sidecar.store.tags import (
    add_item_tag,
    list_user_tags,
    normalize_tag,
    remove_item_tag,
)
from prism_sidecar.store.vectors import (
    count_items_missing_vectors,
    count_vectors,
    items_missing_vectors,
    semantic_search,
    upsert_item_vector,
)
from prism_sidecar.store.webhooks import (
    create_webhook,
    get_webhook,
    list_enabled_webhooks,
    list_webhooks,
    record_webhook_delivery,
    set_webhook_enabled,
)

__all__ = [
    # health
    "health_snapshot",
    # sources
    "create_source",
    "get_source",
    "list_sources",
    "patch_source",
    "delete_source",
    "mark_source_synced",
    "mark_source_error",
    "ensure_default_sources",
    # items
    "list_items",
    "get_item",
    "item_exists_by_url",
    "insert_item_from_raw",
    "get_item_content",
    "update_item_distilled",
    "update_item_status",
    # tags (v0.5)
    "normalize_tag",
    "add_item_tag",
    "remove_item_tag",
    "list_user_tags",
    # vectors (v0.5)
    "upsert_item_vector",
    "count_vectors",
    "items_missing_vectors",
    "count_items_missing_vectors",
    "semantic_search",
    # webhooks (v0.3)
    "create_webhook",
    "get_webhook",
    "list_webhooks",
    "list_enabled_webhooks",
    "set_webhook_enabled",
    "record_webhook_delivery",
    # jobs / history
    "create_job",
    "finish_job",
    "update_job_progress",
    "get_job",
    "list_recent_jobs",
    "is_any_job_running",
    "fail_orphan_running_jobs",
    "write_sync_log",
    "list_sync_history",
    # meta
    "get_meta",
    "set_meta",
    "has_meta",
]
