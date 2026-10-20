"""GET /v1/signals: batch scores of the insider-signal model (research output, never investment advice).

Reads the files the scoring asset writes (lake/ml/signals.parquet and signals.json, see ml/scoring.py) with
DuckDB, so the API needs neither the model nor the ``ml`` extra.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from ..ml import DISCLAIMER, MODEL_NAME

SIGNALS_CACHE_CONTROL = "public, max-age=300, s-maxage=900, stale-while-revalidate=3600"


class SignalFeatures(BaseModel):
    is_buy: float
    role_seniority: float = Field(description="0 CEO, 1 CFO, 2 chair, 3 executive, 4 board member, 5 other")
    log_amount_eur: float | None = Field(description="log10(1 + total amount in EUR)")
    n_insiders: float = Field(description="Distinct insiders in the event")
    cluster_insiders_30d: float = Field(description="Distinct insiders trading the same way in the last 30 days")
    filing_lag_days: float = Field(description="Days from the latest trade to publication")
    log_traded_value_60d: float | None = Field(description="log10 median daily traded value, 60 sessions (size)")
    momentum_20d: float | None = Field(description="Share return over the 20 sessions before publication")


class Signal(BaseModel):
    event_id: str = Field(description="<ISIN>:<buy|sell>:<publication day>")
    country: str
    isin: str
    issuer_name: str
    issuer_lei: str | None
    nature: str = Field(description="buy or sell (open-market share trades)")
    publication_date: dt.date = Field(description="Publication day in the authority's time zone")
    published_at: dt.datetime
    amount_eur: float | None
    n_lines: int = Field(description="Transaction lines aggregated into the event")
    score: float = Field(description="Estimated probability of a positive market-adjusted return over the 20 "
                                     "sessions after the publication-day close (calibrated)")
    features: SignalFeatures


class SignalModel(BaseModel):
    name: str
    version: str
    run_id: str
    trained_through: str | None = Field(description="Last publication day in the training data")
    metrics: dict[str, float | None] = Field(description="Walk-forward, out-of-sample metrics (auc, brier, ...)")
    baseline_auc: float | None = Field(description="Out-of-sample AUC of the logistic-regression baseline")


class SignalDrift(BaseModel):
    status: str = Field(description="stable, moderate, significant (PSI > 0.25) or insufficient_data")
    rows: int
    features: dict[str, dict[str, Any]] = Field(description="PSI, status and missing share per feature")


class SignalsPage(BaseModel):
    disclaimer: str
    model: SignalModel | None = Field(description="Null until a model has been trained and scored")
    scored_at: dt.datetime | None
    snapshot: str | None = Field(description="Lake snapshot the scores were computed from")
    drift: SignalDrift | None
    items: list[Signal]


FEATURE_NAMES = list(SignalFeatures.model_fields)


def signals_router(lake_root: Path) -> APIRouter:
    router = APIRouter()
    scores, meta_path = lake_root / "ml" / "signals.parquet", lake_root / "ml" / "signals.json"

    @router.get("/v1/signals", response_model=SignalsPage, tags=["signals"])
    def list_signals(
        response: Response,
        country: Annotated[list[str] | None, Query(description="ISO country codes, repeatable")] = None,
        isin: str | None = None,
        nature: Literal["buy", "sell"] | None = None,
        since: Annotated[dt.date | None, Query(description="First publication day")] = None,
        min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Any:
        """Model scores of recent insider-trade events, newest first.

        The `insider-signal` model (research/signals/MODEL_CARD.md) estimates, for each open-market buy or sell
        event, the probability that the share beats the market over the next 20 sessions. Out of sample it is
        barely better than a coin flip: read `model.metrics` before reading any score. Research output, not
        investment advice.
        """
        response.headers["Cache-Control"] = SIGNALS_CACHE_CONTROL
        if not scores.exists() or not meta_path.exists():
            return SignalsPage(disclaimer=DISCLAIMER, model=None, scored_at=None, snapshot=None, drift=None, items=[])
        meta = json.loads(meta_path.read_text())
        where, params = ["TRUE"], []
        if country:
            where.append(f"country IN ({', '.join('?' * len(country))})")
            params += [c.upper() for c in country]
        if isin:
            where.append("isin = ?")
            params.append(isin.upper())
        if nature:
            where.append("nature = ?")
            params.append(nature)
        if since:
            where.append("publication_date >= ?")
            params.append(since)
        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)
        con = duckdb.connect()
        try:
            result = con.execute(
                f"SELECT * FROM read_parquet(?) WHERE {' AND '.join(where)} "
                "ORDER BY published_at DESC, event_id LIMIT ?",
                [str(scores), *params, limit],
            )
            columns = [d[0] for d in result.description]
            rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        finally:
            con.close()
        items = [
            Signal(**{k: v for k, v in row.items() if k not in FEATURE_NAMES},
                   features=SignalFeatures(**{k: _finite(row[k]) for k in FEATURE_NAMES}))
            for row in rows
        ]
        model = meta.get("model")
        return SignalsPage(
            disclaimer=meta.get("disclaimer", DISCLAIMER),
            model=SignalModel(**model) if model and model.get("name") == MODEL_NAME else None,
            scored_at=meta.get("scored_at"),
            snapshot=None if meta.get("snapshot") is None else str(meta["snapshot"]),
            drift=SignalDrift(**{k: meta["drift"][k] for k in ("status", "rows", "features")}) if meta.get("drift")
            else None,
            items=items,
        )

    return router


def _finite(value: Any) -> Any:
    return None if isinstance(value, float) and value != value else value
