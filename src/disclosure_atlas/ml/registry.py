"""MLflow tracking and model registry, local by default.

    lake/ml/mlflow.db     tracking and registry store (SQLite: runs, metrics, model versions, aliases)
    lake/ml/artifacts/    run and model artifacts (the model as skops, its feature profile, the summary)

MLflow 3 keeps its plain-folder file store in maintenance mode and refuses it by default, so the local store is
one SQLite file next to a local artifact folder: still no server to run. ``ATLAS_MLFLOW_TRACKING_URI`` points
at a shared tracking server instead (artifacts then follow that server's configuration).

Training registers a new version of ``insider-signal`` and moves the ``champion`` alias to it; scoring loads
``models:/insider-signal@champion``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from . import CHAMPION, MODEL_NAME
from .features import FEATURES
from .model import SKOPS_TRUSTED_TYPES

EXPERIMENT = "insider-signal"
HEADLINE = ("auc", "brier", "brier_skill", "ece", "log_loss", "top_decile_mean_outcome", "auc_buys", "auc_sells")


def _mlflow(lake_root: Path | str) -> Any:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    folder = Path(lake_root).expanduser().resolve() / "ml"
    uri = os.environ.get("ATLAS_MLFLOW_TRACKING_URI")
    if not uri:
        folder.mkdir(parents=True, exist_ok=True)
        uri = f"sqlite:///{folder / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    return mlflow


def _experiment(mlflow: Any, lake_root: Path | str) -> str:
    existing = mlflow.get_experiment_by_name(EXPERIMENT)
    if existing:
        return existing.experiment_id
    artifacts = None
    if not os.environ.get("ATLAS_MLFLOW_TRACKING_URI"):
        artifacts = (Path(lake_root).expanduser().resolve() / "ml" / "artifacts").as_uri()
    return mlflow.create_experiment(EXPERIMENT, artifact_location=artifacts)


def log_training(lake_root: Path | str, model: Any, train: pd.DataFrame, summary: dict[str, Any],
                 feature_profile: dict[str, Any], params: dict[str, Any], tags: dict[str, str] | None = None,
                 artifacts: dict[str, Path] | None = None) -> dict[str, Any]:
    """Log one training run (walk-forward metrics of every candidate, final model) and register the model."""
    mlflow = _mlflow(lake_root)
    from mlflow.models import infer_signature

    experiment = _experiment(mlflow, lake_root)
    trained_through = str(train["publication_date"].max().date())
    with mlflow.start_run(experiment_id=experiment, run_name=f"walk-forward through {trained_through}") as run:
        mlflow.set_tags({"trained_through": trained_through, **(tags or {})})
        mlflow.log_params({**params, "features": ",".join(FEATURES), "n_train": len(train)})
        for candidate, values in summary["pooled"].items():
            mlflow.log_metrics({f"{candidate}.{k}": v for k, v in values.items() if k in HEADLINE})
        for candidate, folds in summary["by_fold"].items():
            for year, values in folds.items():
                mlflow.log_metrics({f"fold.{candidate}.{k}": v for k, v in values.items() if k in HEADLINE},
                                   step=int(year))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "profile.json").write_text(json.dumps(feature_profile, indent=1))
            (Path(tmp) / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
            mlflow.log_artifacts(tmp)
        for path in (artifacts or {}).values():
            mlflow.log_artifact(str(path))
        example = train[FEATURES].tail(5).astype(float)
        info = mlflow.sklearn.log_model(
            model, name="model", registered_model_name=MODEL_NAME, skops_trusted_types=SKOPS_TRUSTED_TYPES,
            input_example=example, pip_requirements=_requirements(),
            signature=infer_signature(example, model.predict_proba(example)[:, 1]),
        )
    client = mlflow.MlflowClient()
    version = str(info.registered_model_version)
    client.set_registered_model_alias(MODEL_NAME, CHAMPION, version)
    client.set_model_version_tag(MODEL_NAME, version, "trained_through", trained_through)
    return {"run_id": run.info.run_id, "version": version, "trained_through": trained_through}


def _requirements() -> list[str]:
    """Pinned serving requirements (skips MLflow's environment inference, which shells out to uv or pip)."""
    from importlib.metadata import version

    return [f"{name}=={version(name)}" for name in ("scikit-learn", "skops", "numpy", "pandas")]


def load_champion(lake_root: Path | str) -> tuple[Any, dict[str, Any], dict[str, Any]] | None:
    """(model, model info, feature profile) of the champion, or None when no model is registered yet."""
    mlflow = _mlflow(lake_root)
    from mlflow.exceptions import MlflowException

    client = mlflow.MlflowClient()
    try:
        version = client.get_model_version_by_alias(MODEL_NAME, CHAMPION)
    except MlflowException:
        return None
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{CHAMPION}")
    run = client.get_run(version.run_id)
    with tempfile.TemporaryDirectory() as tmp:
        path = mlflow.artifacts.download_artifacts(run_id=version.run_id, artifact_path="profile.json", dst_path=tmp)
        feature_profile = json.loads(Path(path).read_text())
    metrics = {k.removeprefix("gradient_boosting."): v for k, v in run.data.metrics.items()
               if k.startswith("gradient_boosting.")}
    info = {
        "name": MODEL_NAME,
        "version": str(version.version),
        "run_id": version.run_id,
        "trained_through": run.data.tags.get("trained_through"),
        "metrics": {k: round(v, 4) for k, v in metrics.items()},
        "baseline_auc": round(run.data.metrics.get("logistic.auc", float("nan")), 4),
    }
    return model, info, feature_profile
