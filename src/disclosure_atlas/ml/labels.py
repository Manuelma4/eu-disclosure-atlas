"""Labels from the WS6 event-study outcomes (research/insider_event_study).

WS6 computes, per event, cumulative abnormal returns from the publication day ("day 0": the first session in
which an outsider could trade). The default label is the market-adjusted CAR over [+1,+20]: it starts at the
day-0 close, so it measures what an outsider could still capture after reading the filing, and it does not
extrapolate a pre-event run-up through a market-model alpha (see the WS6 README, "Method").

    label = 1 if CAR[+1,+20] > 0 else 0
"""

from __future__ import annotations

import pandas as pd

OUTCOME = "ma:[+1,+20]"
HORIZON_SESSIONS = 20
EMBARGO_DAYS = 35
"""Calendar days after publication by which a 20-session outcome is surely known (day 0 can be the next session,
plus 20 sessions, plus holidays). Training rows must be known before a fold starts: see validation.py."""

KEYS = ["isin", "nature", "publication_date"]


def attach_outcomes(events: pd.DataFrame, outcomes: pd.DataFrame, column: str = OUTCOME) -> pd.DataFrame:
    """Events with ``outcome`` (the CAR), ``label`` and ``known_at``; events without an outcome are dropped.

    ``outcomes`` is WS6's event table (``research/insider_event_study/cache/events.parquet``): it has isin,
    nature, pub_date (local publication day) and one column per window.
    """
    right = outcomes.rename(columns={"pub_date": "publication_date"})[[*KEYS, column]].rename(
        columns={column: "outcome"}
    )
    right["publication_date"] = pd.to_datetime(right["publication_date"]).astype("datetime64[us]")
    left = events.assign(publication_date=events["publication_date"].astype("datetime64[us]"))
    merged = left.merge(right.drop_duplicates(KEYS), on=KEYS, how="inner", validate="one_to_one")
    merged = merged[merged["outcome"].notna()].copy()
    merged["label"] = (merged["outcome"] > 0).astype(int)
    merged["known_at"] = merged["publication_date"] + pd.Timedelta(days=EMBARGO_DAYS)
    return merged.sort_values(["publication_date", "event_id"]).reset_index(drop=True)
