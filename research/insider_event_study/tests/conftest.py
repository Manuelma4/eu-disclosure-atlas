"""Make the study's modules importable; skip everything if the `research` extra is not installed."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("statsmodels")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
