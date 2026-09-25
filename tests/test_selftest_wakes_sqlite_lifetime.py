"""Regression for SQLite handles leaked by the wake selftests."""
from __future__ import annotations

import importlib.util
import io
import sqlite3
import unittest
from pathlib import Path

import pytest

SELFTEST = (
    Path(__file__).resolve().parents[1]
    / "skills/multi-session-coordination/scripts/selftest_wakes.py"
)


class _TrackingSQLite:
    """Open real connections and retain them so GC cannot hide leaks."""

    def __init__(self) -> None:
        self.connections: list[sqlite3.Connection] = []

    def connect(self, *args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        self.connections.append(connection)
        return connection


def test_wake_selftests_explicitly_close_every_sqlite_connection():
    spec = importlib.util.spec_from_file_location("candidate_selftest_wakes", SELFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tracker = _TrackingSQLite()
    module.sqlite3 = tracker
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.WakeCLITest)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)

    assert result.wasSuccessful(), stream.getvalue()
    assert tracker.connections, "the regression must observe real SQLite connections"
    for connection in tracker.connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
