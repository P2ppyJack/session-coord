"""The standalone installer explains and asks before changing optional integration."""
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.isolated_subprocess_env import isolated_subprocess_env

ROOT = Path(__file__).resolve().parents[1]


def test_interactive_decline_explains_plugin_and_preserves_standalone(tmp_path):
    if os.name != "posix":
        pytest.skip("PTY prompt exercise requires POSIX")
    import pty

    memory = tmp_path / ".hermes/memories/MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Operator note.\n")
    master, slave = pty.openpty()
    env = isolated_subprocess_env(tmp_path, hermes_home=tmp_path / ".hermes")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "install.py"), "--no-verify"],
        stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
    os.close(slave)
    captured = bytearray()
    answered = False
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                captured.extend(chunk)
                if b"Choose [1]:" in captured and not answered:
                    os.write(master, b"1\n")
                    answered = True
            if process.poll() is not None and not select.select([master], [], [], 0)[0]:
                break
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    text = captured.decode("utf-8", "replace")
    assert answered, text
    assert "works without the plugin" in text
    assert "automatic continuation" in text
    assert "Remove only the plugin" in text
    assert process.returncode == 0, text
    home = tmp_path / ".hermes"
    assert (home / "scripts/session_coord.py").is_file()
    assert not (home / "plugins/session-coord-native").exists()
    assert not (home / "config.yaml").exists()
    memory = (home / "memories/MEMORY.md").read_text()
    assert "--yield" not in memory


@pytest.mark.parametrize("extra", [["--plugin", "keep", "--profile", "../escape"],
                                  ["--plugin", "install", "--plugin-path", "/nonexistent/session-coord-plugin"]])
def test_invalid_selection_refuses_before_board_writes(tmp_path, extra):
    home = tmp_path / "home"
    root = home / ".hermes"
    result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--no-verify", *extra],
                            env=isolated_subprocess_env(home, hermes_home=root),
                            text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not root.exists()
