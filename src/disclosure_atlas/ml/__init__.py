"""Insider-signal model (P8, decision 0014): features from gold, walk-forward validation, MLflow, batch scoring.

    features.py    events (issuer x direction x publication day) and features known at publication
    labels.py      labels from the WS6 event-study outcomes (market-adjusted abnormal return after publication)
    model.py       the calibrated gradient-boosting classifier and the naive baselines
    validation.py  expanding-window walk-forward validation with an embargo (no look-ahead) and metrics
    drift.py       population stability index (PSI) of feature distributions against the training profile
    registry.py    MLflow tracking and model registry (local file store by default)
    scoring.py     batch scoring of recent events into lake/ml/, read by GET /v1/signals

Needs the ``ml`` extra (pandas, scikit-learn, MLflow); the API reads the scores without it.
Research output, never investment advice: see research/signals/MODEL_CARD.md.
"""

MODEL_NAME = "insider-signal"
CHAMPION = "champion"
DISCLAIMER = (
    "Research output from a statistical model, not investment advice. Scores estimate how often similar past "
    "insider trades were followed by a positive market-adjusted return; they carry no information about any "
    "person and must not be used to trade. See research/signals/MODEL_CARD.md."
)
