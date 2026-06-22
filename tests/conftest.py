"""Shared pytest configuration for the radar-vitals test suite.

Defines the --run-dir CLI option and the run_dir fixture used by integration
tests in test_exp004_analysis.py. Integration tests are skipped by default;
supply --run-dir <path> or set EXP004_RUN_DIR to activate them.
"""
import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-dir",
        action="store",
        default=None,
        help="Path to a completed exp004 results directory for integration tests.",
    )


@pytest.fixture
def run_dir(request):
    d = request.config.getoption("--run-dir") or os.environ.get("EXP004_RUN_DIR")
    if d is None:
        pytest.skip("--run-dir not provided")
    return Path(d)
