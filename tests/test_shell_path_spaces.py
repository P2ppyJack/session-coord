"""The shipped POSIX verification scripts must support paths with spaces."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX smoke test; Windows shell coverage runs in Git Bash CI")
@pytest.mark.parametrize("name", ["selftest.sh", "selftest_priority.sh", "selftest_cron.sh", "selftest_toggle.sh"])
def test_selftests_from_a_directory_with_spaces(tmp_path, name):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "source with spaces"
    shutil.copytree(root / "skills/multi-session-coordination/scripts", scripts)
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), HERMES_HOME=str(tmp_path / "home"))
    result = subprocess.run(
        ["bash", str(scripts / name)], env=env, cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
