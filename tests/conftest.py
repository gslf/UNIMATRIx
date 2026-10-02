"""Shared test configuration: isolated caches."""

import pytest


@pytest.fixture(autouse=True, scope="session")
def isolated_cache(tmp_path_factory):
    """Feasibility certificates never leak between test sessions or into the user's cache."""
    import os

    os.environ["UNIMATRIX_CACHE_DIR"] = str(tmp_path_factory.mktemp("unimatrix-cache"))
    yield


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    """Infrastructure retries are tested for bookkeeping, not for their pacing."""
    monkeypatch.setattr("unimatrix.benchmark.runner.RETRY_BACKOFF", (0, 0))
