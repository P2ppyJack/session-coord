"""The cron selftest loader must not depend on shell PYTHONPATH conversion."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/multi-session-coordination/scripts"

@pytest.mark.parametrize("folder", ["installed", "installed scripts"])
def test_cron_loader_finds_sibling_without_pythonpath(tmp_path, folder):
    script = (SCRIPTS / "selftest_cron.sh").read_text()
    block = script.split("<<'PYMOD'\n", 1)[1].split("\nPYMOD", 1)[0]
    installed = tmp_path / folder
    installed.mkdir()
    for name in ["session_coord.py", "session_coord_wakes.py"]:
        shutil.copy2(SCRIPTS / name, installed / name)
    profiles = tmp_path / "profiles"
    (profiles / "researcher/cron").mkdir(parents=True)
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [{"id": "sharedid00001", "name": "default owner", "enabled": True}]}))
    (profiles / "researcher/cron/jobs.json").write_text(json.dumps({"jobs": [
        {"id": "researcherjb1", "name": "morning digest", "enabled": True},
        {"id": "researcherjb2", "name": "[bot:researcher] tagged already", "enabled": True},
        {"id": "sharedid00001", "name": "bot impostor", "enabled": True}
    ]}))
    env = os.environ.copy()
    env.update({"PYTHONPATH": "", "PYTHONUTF8": "1", "HOME": str(tmp_path),
                "USERPROFILE": str(tmp_path), "HERMES_HOME": str(tmp_path / ".hermes"),
                "HERMES_COORD_CRON_JOBS": str(jobs), "HERMES_COORD_PROFILES_DIR": str(profiles)})
    result = subprocess.run([sys.executable, "-", str(installed / "session_coord.py")],
                            input=block, text=True, capture_output=True, cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["[bot:researcher] tagged already", "default owner", "researcher"]
