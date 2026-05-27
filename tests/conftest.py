"""Shared test fixtures.

All fixtures here use synthetic data — no real Benchling tenant or sequencing
run is required to run the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
