"""Batch scoring: recent gold events through the champion model, written next to the lake for the API.

    lake/ml/signals.parquet   one row per event: identifiers, features, score (atomically replaced)
    lake/ml/signals.json      model version and metrics, the lake snapshot scored, drift report, disclaimer

The API reads these two files only (no pandas, scikit-learn or MLflow in the API image).
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import DISCLAIMER, drift
from .features import CLUSTER_DAYS, FEATURES, aggregate_events, build_features, cached_prices, load_trades
from .registry import load_champion

SIGNALS = Path("ml") / "signals.parquet"
META = Path("ml") / "signals.json"
OUTPUT_COLUMNS = ["event_id", "country", "isin", "issuer_name", "issuer_lei", "nature", "publication_date",
                  "published_at", "amount_eur", "n_lines", *FEATURES, "score"]


def _clean(value: Any) -> Any:
    """JSON-safe: NaN and infinities become null."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def score(lake: Any, days: int = 365, champion: tuple[Any, dict[str, Any], dict[str, Any]] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """Score the events published in the last ``days`` days; returns the metadata written to signals.json."""
    champion = champion or load_champion(lake.root)
    if champion is None:
        return {"status": "no_model", "rows": 0}
    model, info, reference = champion
    now = now or datetime.now(UTC)
    cutoff = pd.Timestamp(now - timedelta(days=days)).tz_convert(None).normalize()
    snapshot = lake.version()
    con = lake.connect(as_of=snapshot)
    try:
        # Extra history so that the trailing cluster count of the first scored events is complete.
        trades = load_trades(con, pd.Timestamp(now - timedelta(days=days + CLUSTER_DAYS + 1)))
    finally:
        con.close()
    events = aggregate_events(trades) if len(trades) else pd.DataFrame(columns=OUTPUT_COLUMNS)
    if len(events):
        events = events[events["publication_date"] >= cutoff].reset_index(drop=True)
        prices = cached_prices(lake.root, events["isin"].tolist(), cutoff)
        frame = build_features(events, prices)
        frame["score"] = model.predict_proba(frame[FEATURES])[:, 1]
    else:
        frame = events.assign(score=pd.Series(dtype=float))
    frame = frame[OUTPUT_COLUMNS].sort_values(["published_at", "event_id"], ascending=[False, True])
    frame["publication_date"] = pd.to_datetime(frame["publication_date"]).dt.date
    meta = _clean({
        "status": "ok",
        "scored_at": now.isoformat(timespec="seconds"),
        "snapshot": snapshot,
        "window_days": days,
        "rows": len(frame),
        "model": info,
        "drift": drift.report(reference, frame),
        "disclaimer": DISCLAIMER,
    })
    target = Path(lake.root) / SIGNALS
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, target)
    meta_tmp = (Path(lake.root) / META).with_suffix(".tmp")
    meta_tmp.write_text(json.dumps(meta, indent=1, default=str) + "\n")
    os.replace(meta_tmp, Path(lake.root) / META)
    return meta
