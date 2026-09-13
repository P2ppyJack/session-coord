from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.py"
PAYLOAD = {
    "session_coord.py": True,
    "session_coord_wakes.py": False,
    "coord_guard.sh": False,
    "selftest.sh": True,
    "selftest_priority.sh": True,
    "selftest_cron.sh": True,
    "selftest_toggle.sh": True,
    "selftest_wakes.py": True,
    "coord_resume_watchdog.py": True,
}


def _installer_command(dest: Path, state: Path, *, no_verify: bool = False) -> list[str]:
    command = [
        sys.executable,
        str(INSTALLER),
        "--dest",
        str(dest),
        "--state-dir",
        str(state),
        "--no-wire-memory",
        "--no-wire-bots",
        "--no-wire-profiles",
        "--no-skill",
    ]
    if no_verify:
        command.append("--no-verify")
    return command


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, input="", text=True, capture_output=True, check=False)


def test_isolated_installer_packages_resume_stack_and_is_idempotent(tmp_path: Path):
    dest = tmp_path / "installed-scripts"
    state = tmp_path / "installer-state"
    state.mkdir()
    protected = {
        state / "session_coordination.db": b"do-not-touch-board\x00",
        state / "cron_resources.json": b'{"do_not_touch":true}\n',
    }
    for path, content in protected.items():
        path.write_bytes(content)

    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "isolated-hermes-home")
    env.pop("HERMES_COORD_ID", None)

    first = _run(_installer_command(dest, state), env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "selftest_wakes.py" in first.stdout
    assert "All suites green" in first.stdout
    for name, executable in PAYLOAD.items():
        installed = dest / name
        assert installed.is_file(), name
        # Windows invokes these scripts through Python/Bash, not POSIX mode bits.
        if executable and os.name == "posix":
            assert installed.stat().st_mode & stat.S_IXUSR, name
        source = ROOT / "skills/multi-session-coordination/scripts" / name
        assert installed.read_bytes() == source.read_bytes(), name
    for path, content in protected.items():
        assert path.read_bytes() == content

    scratch = tmp_path / "engine-state"
    scratch.mkdir()
    engine_env = env.copy()
    engine_env.update(
        {
            "HERMES_COORD_DB": str(scratch / "board.db"),
            "HERMES_COORD_DISABLED_FILE": str(scratch / "disabled"),
            "HERMES_COORD_CRON_MANIFEST": str(scratch / "cron.json"),
            "HERMES_COORD_CRON_JOBS": str(scratch / "jobs.json"),
            "HERMES_COORD_PROFILES_DIR": str(scratch / "profiles"),
        }
    )
    help_result = _run([sys.executable, str(dest / "session_coord.py"), "--help"], engine_env)
    assert help_result.returncode == 0, help_result.stderr
    assert "wake-reconcile" in help_result.stdout
    reconcile = _run(
        [sys.executable, str(dest / "session_coord.py"), "wake-reconcile", "--json"],
        engine_env,
    )
    assert reconcile.returncode == 0, reconcile.stdout + reconcile.stderr
    payload = json.loads(reconcile.stdout)
    action_and_diagnostic_keys = {
        "enqueued",
        "episodes_checked",
        "stale_events_canceled",
        "unknown_leases",
    }
    assert action_and_diagnostic_keys <= set(payload)
    assert all(isinstance(payload[key], int) for key in action_and_diagnostic_keys)
    python_suite = _run([sys.executable, str(dest / "selftest_wakes.py")], engine_env)
    assert python_suite.returncode == 0, python_suite.stdout + python_suite.stderr

    before = {
        path.name: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in dest.iterdir()
        if path.is_file()
    }
    second = _run(_installer_command(dest, state, no_verify=True), env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert second.stdout.count("unchanged") >= len(PAYLOAD)
    after = {
        path.name: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in dest.iterdir()
        if path.is_file()
    }
    assert after == before
    assert not list(dest.glob("*.bak-*"))
    for path, content in protected.items():
        assert path.read_bytes() == content
