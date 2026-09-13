from __future__ import annotations

import builtins
import errno
import importlib.util
import json
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "multi-session-coordination"
    / "scripts"
    / "coord_resume_watchdog.py"
)


def _board_fixture(tmp_path: Path, payload: dict, *, exit_code: int = 0) -> tuple[Path, Path]:
    board = tmp_path / "board.py"
    calls = tmp_path / "calls.jsonl"
    board.write_text(
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "with calls.open('a') as handle: handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print(json.dumps({payload!r}))\n"
        f"raise SystemExit({exit_code})\n"
    )
    return board, calls


def _run(tmp_path: Path, board: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--board-script",
            str(board),
            "--lock-file",
            str(tmp_path / "watchdog.lock"),
            *extra,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_watchdog_is_silent_when_reconcile_has_no_actions(tmp_path: Path):
    board, calls = _board_fixture(
        tmp_path,
        {
            "enqueued": 0,
            "stale_events_canceled": 0,
            "unknown_leases": 0,
            "episodes_checked": 9,
        },
    )

    result = _run(tmp_path, board)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert json.loads(calls.read_text().splitlines()[0]) == ["wake-reconcile", "--json"]


@pytest.mark.parametrize("action_key", ["enqueued", "stale_events_canceled", "unknown_leases"])
def test_watchdog_reports_each_actual_reconcile_action(tmp_path: Path, action_key: str):
    payload = {
        "enqueued": 0,
        "stale_events_canceled": 0,
        "unknown_leases": 0,
        "episodes_checked": 4,
    }
    payload[action_key] = 1
    board, calls = _board_fixture(tmp_path, payload)

    result = _run(tmp_path, board)

    assert result.returncode == 0
    assert json.loads(result.stdout)[action_key] == 1
    assert json.loads(calls.read_text().splitlines()[0]) == ["wake-reconcile", "--json"]


def test_json_flag_reports_idle_diagnostics(tmp_path: Path):
    payload = {
        "enqueued": 0,
        "stale_events_canceled": 0,
        "unknown_leases": 0,
        "episodes_checked": 2,
    }
    board, _ = _board_fixture(tmp_path, payload)

    result = _run(tmp_path, board, "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout) == payload


def test_watchdog_fails_inert_when_board_is_not_installed(tmp_path: Path):
    missing = tmp_path / "missing-board.py"

    result = _run(tmp_path, missing)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_watchdog_reports_timeout(tmp_path: Path):
    board = tmp_path / "slow-board.py"
    board.write_text("import time\ntime.sleep(1)\n")

    result = _run(tmp_path, board, "--timeout", "0.01")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "coord_resume_watchdog: wake-reconcile timed out\n"


def test_watchdog_reports_board_error(tmp_path: Path):
    board = tmp_path / "broken-board.py"
    board.write_text("import sys\nprint('board exploded', file=sys.stderr)\nraise SystemExit(7)\n")

    result = _run(tmp_path, board)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "coord_resume_watchdog: board exploded\n"


class _FakeMsvcrt(types.ModuleType):
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, acquire_error: OSError | None = None):
        super().__init__("msvcrt")
        self.acquire_error = acquire_error
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, count: int) -> None:
        self.calls.append((fd, mode, count))
        if mode == self.LK_NBLCK and self.acquire_error is not None:
            raise self.acquire_error


def _load_as_windows(monkeypatch: pytest.MonkeyPatch, backend: _FakeMsvcrt):
    original_os_name = os.name
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", backend)
    real_import = builtins.__import__

    def import_without_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ModuleNotFoundError("No module named 'fcntl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_fcntl)
    spec = importlib.util.spec_from_file_location(f"watchdog_{uuid.uuid4().hex}", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        monkeypatch.setattr(os, "name", original_os_name)
    return module


def test_windows_import_and_nonblocking_lock_do_not_require_fcntl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backend = _FakeMsvcrt()
    watchdog = _load_as_windows(monkeypatch, backend)

    with (tmp_path / "watchdog.lock").open("a+b") as handle:
        assert watchdog._acquire_lock(handle) is True
        assert handle.seek(0, os.SEEK_END) >= 1
        watchdog._release_lock(handle)

    assert [call[1:] for call in backend.calls] == [
        (backend.LK_NBLCK, 1),
        (backend.LK_UNLCK, 1),
    ]


def test_windows_lock_contention_is_quiet_but_unexpected_errors_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    contention = _FakeMsvcrt(OSError(errno.EACCES, "locked"))
    watchdog = _load_as_windows(monkeypatch, contention)
    with (tmp_path / "contended.lock").open("a+b") as handle:
        assert watchdog._acquire_lock(handle) is False

    unexpected = _FakeMsvcrt(OSError(errno.EIO, "device error"))
    watchdog = _load_as_windows(monkeypatch, unexpected)
    with (tmp_path / "broken.lock").open("a+b") as handle:
        with pytest.raises(OSError, match="device error"):
            watchdog._acquire_lock(handle)


def test_windows_unexpected_lock_error_is_reported_without_running_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    watchdog = _load_as_windows(
        monkeypatch, _FakeMsvcrt(OSError(errno.EIO, "device error"))
    )
    board, calls = _board_fixture(tmp_path, {"enqueued": 1})
    args = types.SimpleNamespace(
        board_script=board,
        lock_file=tmp_path / "watchdog.lock",
        timeout=1,
        json=False,
    )

    assert watchdog._run(args) == 1
    assert capsys.readouterr().err == "coord_resume_watchdog: lock failed: [Errno 5] device error\n"
    assert not calls.exists()


def test_posix_overlapping_runs_execute_exactly_one_reconcile(tmp_path: Path):
    board = tmp_path / "slow-board.py"
    calls = tmp_path / "calls.jsonl"
    started = tmp_path / "started"
    board.write_text(
        "import json, pathlib, sys, time\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        f"started = pathlib.Path({str(started)!r})\n"
        "with calls.open('a') as handle: handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "started.write_text('ready')\n"
        "time.sleep(0.5)\n"
        "print(json.dumps({'enqueued': 0}))\n"
    )
    command = [
        sys.executable,
        str(SCRIPT),
        "--board-script",
        str(board),
        "--lock-file",
        str(tmp_path / "watchdog.lock"),
    ]
    first = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if started.is_file():
            break
        import time

        time.sleep(0.01)
    assert started.is_file()
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    first_stdout, first_stderr = first.communicate(timeout=5)

    assert (first.returncode, first_stdout, first_stderr) == (0, "", "")
    assert (second.returncode, second.stdout, second.stderr) == (0, "", "")
    assert len(calls.read_text().splitlines()) == 1
