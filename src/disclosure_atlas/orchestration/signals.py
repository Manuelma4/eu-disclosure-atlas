"""Batch scoring of the insider-signal model as a Dagster asset, with a feature-drift check (P8).

    gold/insider_transactions -> ml/insider_signals   champion model from the MLflow registry, scores of the
                                                      last year of events into lake/ml/ (read by /v1/signals)

Training stays a deliberate, reviewed step (research/signals/train.py): the asset only scores with the model the
registry's ``champion`` alias points to, and materializes nothing when no model is registered. Needs the ``ml``
extra in the code location.
"""

import json
from pathlib import Path

import dagster as dg

POOL = "ducklake"
SCORING_DAYS = 365


def signal_definitions(lake_resource: type[dg.ConfigurableResource]) -> dg.Definitions:
    """``lake_resource`` is definitions.LakeResource (passed in to avoid a circular import)."""

    @dg.asset(
        key=["ml", "insider_signals"], deps=[dg.AssetKey(["gold", "insider_transactions"])], group_name="ml",
        kinds={"mlflow", "python"}, pool=POOL,
        description="Insider-signal scores of the last year of open-market events (research output, not advice).",
    )
    def insider_signals(context: dg.AssetExecutionContext, lake: lake_resource) -> dg.MaterializeResult:
        try:
            from ..ml.scoring import score
        except ImportError:
            context.log.warning("The ml extra is not installed in this code location: nothing scored")
            return dg.MaterializeResult(metadata={"status": "no_ml_extra", "rows": 0})
        meta = score(lake.get(), days=SCORING_DAYS)
        if meta["status"] == "no_model":
            context.log.warning("No champion model in the MLflow registry: run research/signals/train.py first")
            return dg.MaterializeResult(metadata={"status": "no_model", "rows": 0})
        drift = meta["drift"]
        return dg.MaterializeResult(metadata={
            "status": meta["status"],
            "rows": meta["rows"],
            "model_version": meta["model"]["version"],
            "trained_through": meta["model"]["trained_through"] or "",
            "snapshot": meta["snapshot"],
            "drift_status": drift["status"],
            "max_psi": max((f["psi"] or 0.0 for f in drift["features"].values()), default=0.0),
        })

    @dg.asset_check(asset=insider_signals, name="feature_drift", blocking=False,
                    description="No feature's PSI against the training distribution above 0.25.")
    def feature_drift(lake: lake_resource) -> dg.AssetCheckResult:
        path = Path(lake.get().root) / "ml" / "signals.json"
        if not path.exists():
            return dg.AssetCheckResult(passed=True, description="No scores yet", metadata={"status": "no_scores"})
        drift = json.loads(path.read_text())["drift"]
        shifted = {name: f["psi"] for name, f in drift["features"].items() if f["status"] == "significant"}
        return dg.AssetCheckResult(
            passed=not shifted, severity=dg.AssetCheckSeverity.WARN,
            description=f"Drift: {drift['status']}" + (f" ({', '.join(sorted(shifted))})" if shifted else ""),
            metadata={"status": drift["status"], "rows": drift["rows"],
                      **{f"psi.{name}": f["psi"] if f["psi"] is not None else -1.0
                         for name, f in drift["features"].items()}},
        )

    job = dg.define_asset_job("insider_signals_job", selection=dg.AssetSelection.assets(insider_signals),
                              description="Score recent events with the champion insider-signal model.")
    daily = dg.ScheduleDefinition(
        name="insider_signals_daily", job=job, cron_schedule="15 7 * * *", execution_timezone="Europe/Paris",
        default_status=dg.DefaultScheduleStatus.RUNNING, description="Every day at 07:15 Paris time.",
    )
    return dg.Definitions(assets=[insider_signals], asset_checks=[feature_drift], jobs=[job], schedules=[daily])
