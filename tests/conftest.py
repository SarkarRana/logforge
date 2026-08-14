"""Shared test fixtures and helpers."""

import logging
from typing import Iterator

import pytest

from logcore.logger import _loggers


def close_logcore_handlers() -> None:
    """Detach and close every handler on every cached LogCore logger.

    Call this before deleting a log file or temp directory. A
    ``RotatingFileHandler`` keeps its file open, and Windows refuses to unlink
    or rmdir an open file — so tests that skip this pass on POSIX and fail on
    the Windows CI job.
    """
    for log in list(_loggers.values()):
        for handler in list(log._logger.handlers):
            log._logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    _loggers.clear()


@pytest.fixture(autouse=True)
def _reset_logcore_state() -> Iterator[None]:
    """Leave no handlers, cached loggers or queue listeners between tests."""
    yield

    import logcore

    logcore.shutdown()
    close_logcore_handlers()

    # Tests that call configure_stdlib touch the root logger; make sure a
    # failure mid-test cannot leak handlers into unrelated tests.
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.__class__.__module__.startswith("logcore"):
            root.removeHandler(handler)
