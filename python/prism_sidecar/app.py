"""FastAPI app — Prism sidecar."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from prism_sidecar import __version__
from prism_sidecar import store
from prism_sidecar.models import HealthInfo, Source, SourceCreate


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[prism-sidecar] v{__version__} started on http://127.0.0.1:8765")
    yield
    print("[prism-sidecar] shutting down")


app = FastAPI(
    title="Prism Sidecar",
    version=__version__,
    description="AI news & knowledge distillation sidecar",
    lifespan=lifespan,
)

# Allow the Tauri webview (and Vite dev server) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Health -----

@app.get("/health", response_model=HealthInfo, response_model_by_alias=True)
def health() -> HealthInfo:
    snap = store.health_snapshot()
    return HealthInfo(**snap)


# ----- Sources -----

@app.get("/api/sources", response_model=list[Source], response_model_by_alias=True)
def list_sources() -> list[Source]:
    return store.list_sources()


@app.get("/api/sources/{source_id}", response_model=Source, response_model_by_alias=True)
def get_source(source_id: str) -> Source:
    s = store.get_source(source_id)
    if not s:
        raise HTTPException(404, f"source {source_id} not found")
    return s


@app.post("/api/sources", response_model=Source, response_model_by_alias=True)
def create_source(payload: SourceCreate) -> Source:
    return store.create_source(
        name=payload.name,
        kind=payload.kind,
        url=payload.url,
        enabled=payload.enabled,
    )


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str) -> dict:
    ok = store.delete_source(source_id)
    if not ok:
        raise HTTPException(404, f"source {source_id} not found")
    return {"ok": True}


# ----- Items -----

@app.get("/api/items", response_model_by_alias=True)
def list_items(source_id: str | None = None, status: str | None = None, q: str | None = None):
    return store.list_items(source_id=source_id, status=status, q=q)


@app.get("/api/items/{item_id}", response_model_by_alias=True)
def get_item(item_id: str):
    it = store.get_item(item_id)
    if not it:
        raise HTTPException(404, f"item {item_id} not found")
    return it


# ----- Sync -----

class SyncResult(BaseModel):
    triggered: int
    started_at: datetime
    finished_at: datetime


@app.post("/api/sync", response_model=SyncResult)
def trigger_sync() -> SyncResult:
    # v0.1: no-op (data is static). v0.2: enqueue real fetch jobs.
    started = datetime.now(timezone.utc)
    finished = datetime.now(timezone.utc)
    return SyncResult(triggered=len(store.list_sources()), started_at=started, finished_at=finished)
