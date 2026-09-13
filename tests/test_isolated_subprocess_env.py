from __future__ import annotations

from pathlib import Path

from tests import test_install_upgrade


def test_install_runner_preserves_only_required_windows_runtime_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SYSTEMROOT", r"C:\\Windows")
    monkeypatch.setenv("WINDIR", r"C:\\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\\Windows\\System32\\cmd.exe")
    monkeypatch.setenv("UNRELATED_PARENT_SECRET", "must-not-leak")
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(test_install_upgrade.subprocess, "run", fake_run)
    home = tmp_path / "isolated home"

    test_install_upgrade._run(
        home,
        home / "memories" / "MEMORY.md",
        home / "scripts",
        home / "state",
    )

    env = captured["env"]
    assert env["HOME"] == str(home)
    assert env["USERPROFILE"] == str(home)
    assert env["SYSTEMROOT"] == r"C:\\Windows"
    assert env["WINDIR"] == r"C:\\Windows"
    assert env["COMSPEC"] == r"C:\\Windows\\System32\\cmd.exe"
    assert "UNRELATED_PARENT_SECRET" not in env
