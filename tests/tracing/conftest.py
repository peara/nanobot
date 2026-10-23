from __future__ import annotations

import pytest

from nanobot.tracing import shutdown_tracing


@pytest.fixture(autouse=True)
def _isolate_tracing_runtime():
    """Tracing state is module-global; never leak a runtime between tests."""
    shutdown_tracing()
    yield
    shutdown_tracing()
