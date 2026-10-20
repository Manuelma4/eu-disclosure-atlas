"""GET /v1/exports: the gold tables as Parquet files, for DuckDB (including DuckDB-WASM in the browser).

Files are written once per data snapshot under lake/exports/<snapshot>/ and served with HTTP range requests,
so a browser reads only the columns and row groups it needs.
"""

from __future__ import annotations

import shutil
import threading
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..models import Dataset
from .readmodel import ReadModel, Snapshot

PARQUET = "application/vnd.apache.parquet"
EXPORT_CACHE_CONTROL = "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"
# Sorted so row-group statistics prune well on the explorer's typical filters (time, then issuer).
ORDER = {Dataset.INSIDER_TRANSACTIONS: "published_at, issuer_lei", Dataset.NET_SHORT_POSITIONS: "position_date"}


class ExportName(StrEnum):
    INSIDER_TRANSACTIONS = "insider_transactions.parquet"
    NET_SHORT_POSITIONS = "net_short_positions.parquet"


class ExportFile(BaseModel):
    dataset: str
    path: str = Field(description="Relative URL of the Parquet file")
    rows: int
    bytes: int
    snapshot: str


def exports_router(read_model: ReadModel, root: Path) -> APIRouter:
    router = APIRouter()
    lock = threading.Lock()

    def ensure(snap: Snapshot, dataset: Dataset) -> tuple[Path, int]:
        folder = root / snap.version
        target = folder / f"{dataset.value}.parquet"
        count = snap.one(f"SELECT count(*) AS n FROM {dataset.value}")["n"]  # type: ignore[index]
        with lock:
            if not target.exists():
                folder.mkdir(parents=True, exist_ok=True)
                tmp = str(target.with_suffix(".tmp")).replace("'", "''")
                snap.cursor.execute(
                    f"COPY (SELECT * FROM {dataset.value} ORDER BY {ORDER[dataset]}) TO '{tmp}' "
                    "(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)"
                )
                Path(tmp).replace(target)
                for old in root.iterdir():  # keep only the current snapshot
                    if old.is_dir() and old.name != snap.version:
                        shutil.rmtree(old, ignore_errors=True)
        return target, count

    @router.get("/v1/exports", response_model=list[ExportFile], tags=["exports"])
    def list_exports() -> Any:
        """Gold tables available as Parquet for the current data snapshot."""
        with read_model.snapshot() as snap:
            files = []
            for dataset in Dataset:
                path, rows = ensure(snap, dataset)
                files.append(ExportFile(dataset=dataset.value, path=f"/v1/exports/{dataset.value}.parquet",
                                        rows=rows, bytes=path.stat().st_size, snapshot=snap.version))
            return files

    @router.head("/v1/exports/{name}", include_in_schema=False)
    @router.get(
        "/v1/exports/{name}",
        tags=["exports"],
        response_class=FileResponse,
        responses={200: {"content": {PARQUET: {}}, "description": "Parquet file (supports Range requests)"}},
    )
    def get_export(name: ExportName) -> FileResponse:
        """One gold table as Parquet (zstd). Supports `Range` requests, so DuckDB reads it remotely."""
        dataset = Dataset(name.value.removesuffix(".parquet"))
        with read_model.snapshot() as snap:
            if dataset not in ORDER:
                raise HTTPException(HTTPStatus.NOT_FOUND, f"No export for {dataset}")
            path, _ = ensure(snap, dataset)
            version = snap.version
        return FileResponse(
            path,
            media_type=PARQUET,
            filename=f"{dataset.value}-{version}.parquet",
            content_disposition_type="inline",
            headers={"Cache-Control": EXPORT_CACHE_CONTROL, "X-Atlas-Snapshot": version},
        )

    return router
