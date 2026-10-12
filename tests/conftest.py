"""Shared test configuration: no pacing between infrastructure retries."""

import pytest


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    """Infrastructure retries are tested for bookkeeping, not for their pacing."""
    monkeypatch.setattr("unimatrix.polis.runner.RETRY_BACKOFF", (0, 0))
