"""Exercise the installer against a real compatible Hermes checkout and plugin."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_real_standalone_add_upgrade_remove_roundtrip(tmp_path):
    host = os.environ.get("SESSION_COORD_TEST_HERMES_ROOT")
    package = os.environ.get("SESSION_COORD_TEST_PLUGIN_SOURCE")
    if not host or not package:
        pytest.skip("set the real candidate Hermes and plugin paths for integration")
    home = tmp_path / "user home with spaces"
    hermes_home = home / ".hermes"
    memory = hermes_home / "memories/MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Operator context stays.\n", encoding="utf-8")
    config = hermes_home / "config.yaml"
    config.write_text("model:\n  default: local-review-only\nterminal:\n  backend: local\n", encoding="utf-8")
    source = tmp_path / "plugin source"
    shutil.copytree(package, source, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache"))
    env = {"HOME": str(home), "HERMES_HOME": str(hermes_home), "PATH": os.environ["PATH"],
           "PYTHONPATH": host, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    commands = []

    def run(argv, expected=0, choice=None):
        if choice is None:
            result = subprocess.run(argv, env=env, cwd=host, capture_output=True,
                                    text=True, encoding="utf-8", timeout=75, check=False)
        else:
            import pty
            master, slave = pty.openpty()
            process = subprocess.Popen(argv, env=env, cwd=host, stdin=slave,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            os.close(slave)
            try:
                os.write(master, (choice + "\n").encode())
                stdout, stderr = process.communicate(timeout=75)
                result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            finally:
                os.close(master)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        commands.append({"argv": argv, "rc": result.returncode, "out": result.stdout, "err": result.stderr})
        (tmp_path / "commands.json").write_text(json.dumps(commands, indent=2), encoding="utf-8")
        assert result.returncode == expected, result.stdout + result.stderr
        return result

    def commit(message):
        run(["git", "-C", str(source), "add", "."])
        run(["git", "-C", str(source), "-c", "user.name=Installer Test", "-c",
             "user.email=installer@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", message])
        return run(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.strip()

    run(["git", "init", "-q", str(source)])
    first_sha = commit("local installer test source")
    installer = [sys.executable, str(ROOT / "install.py"), "--no-verify"]
    host_flags = ["--hermes", sys.executable, "--hermes-arg=-m", "--hermes-arg=hermes_cli.main"]
    run(installer + ["--plugin", "keep"])
    installed = hermes_home / "plugins/session-coord-native"
    assert not installed.exists()
    board_memory = memory.read_bytes()
    scripts = hermes_home / "scripts"
    board = [sys.executable, str(scripts / "session_coord.py")]
    run(board + ["register", "--id", "persistent-user-claim", "--task", "preserve me"])
    run(board + ["claim", "--id", "persistent-user-claim", "--res", "res:installer-test"])
    db = hermes_home / "state/session_coordination.db"
    manifest = hermes_home / "state/cron_resources.json"
    manifest.write_text('{"user-owned":true}\n', encoding="utf-8")
    receipt = hermes_home / "state/user-receipts.json"
    receipt.write_text('{"keep":"existing receipt"}\n', encoding="utf-8")
    protected = {p: p.read_bytes() for p in [db, manifest, receipt]}
    protected.update({p: p.read_bytes() for p in scripts.iterdir() if p.is_file()})
    add = installer + ["--plugin", "install", "--plugin-path", str(source), *host_flags]
    run(installer + ["--plugin-path", str(source), *host_flags], choice="2")
    assert run(["git", "-C", str(installed), "rev-parse", "HEAD"]).stdout.strip() == first_sha
    assert memory.read_text().count("<!-- BEGIN session-coord managed hermes-native-v1 -->") == 1
    native_memory = memory.read_bytes()
    config_after = config.read_bytes()
    run(add)
    assert memory.read_bytes() == native_memory
    assert config.read_bytes() == config_after
    # A default/declined rerun preserves an already-installed plugin, too.
    run(installer + ["--plugin", "keep"])
    assert installed.is_dir()
    assert memory.read_bytes() == native_memory
    with (source / "README.md").open("a", encoding="utf-8") as stream:
        stream.write("\nLocal integration upgrade fixture.\n")
    second_sha = commit("local installer test revision")
    assert second_sha != first_sha
    run(add)
    assert run(["git", "-C", str(installed), "rev-parse", "HEAD"]).stdout.strip() == second_sha
    # Removal takes no source path and must not update or remove board files.
    remove = installer + ["--plugin", "remove", *host_flags]
    before_check = {p: p.read_bytes() for p in [memory, config, db]}
    run(remove + ["--check"])
    assert installed.is_dir()
    assert all(p.read_bytes() == before for p, before in before_check.items())
    run(installer + host_flags, choice="3")
    assert not installed.exists()
    remaining = memory.read_bytes()
    assert board_memory in remaining
    assert b"hermes-native-wire" not in remaining
    assert all(p.read_bytes() == before for p, before in protected.items())
    assert "local-review-only" in config.read_text()
    before_repeat = {p: p.read_bytes() for p in [memory, config, db, manifest, receipt]}
    run(remove)
    assert all(p.read_bytes() == before for p, before in before_repeat.items())
    # Re-adding after removal uses the same user state and the latest revision.
    run(add)
    assert installed.is_dir()
    assert all(p.read_bytes() == before for p, before in protected.items())
    status = json.loads(run(board + ["status", "--json"]).stdout)
    assert any(c.get("resource") == "res:installer-test" for c in status["held_claims"])
    report = {"passed": True, "first_sha": first_sha, "upgraded_sha": second_sha,
              "protected_files": {str(p.relative_to(hermes_home)): hashlib.sha256(v).hexdigest() for p, v in protected.items()},
              "commands_log": str(tmp_path / "commands.json")}
    (tmp_path / "lifecycle-proof.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
