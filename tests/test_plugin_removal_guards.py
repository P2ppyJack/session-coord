"""Destructive-path refusals preserve board and unrelated profile data."""
import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REMOVE = importlib.import_module("hermes_remove")
SETUP = REMOVE.setup


class FaultRunner:
    def __init__(self, home, fault=None):
        self.home = home
        self.fault = fault
        self.calls = []
        self.enabled = True

    def __call__(self, argv, timeout=60):
        self.calls.append(list(argv))
        command = list(argv[3:])
        rc, out = 0, ""
        if command == ["config", "path"]:
            out = str(self.home / "config.yaml")
        elif command == ["config", "get", "plugins", "--json"]:
            out = "unreadable" if self.fault == "config" else json.dumps({
                "enabled": [SETUP.PLUGIN_NAME] if self.enabled else ["unrelated"],
                "disabled": [] if self.enabled else [SETUP.PLUGIN_NAME]})
        elif command == ["plugins", "disable", SETUP.PLUGIN_NAME]:
            if self.fault == "disable":
                rc, out = 1, "disable fixture failure"
            else:
                self.enabled = False
        elif command == ["plugins", "remove", SETUP.PLUGIN_NAME]:
            if self.fault == "remove":
                rc, out = 1, "remove fixture failure"
            else:
                shutil.rmtree(self.home / "plugins" / SETUP.PLUGIN_NAME)
        else:
            raise AssertionError(command)
        return SETUP.CommandResult(argv, rc, out, "")


def fixture(home):
    target = home / "plugins" / SETUP.PLUGIN_NAME
    target.mkdir(parents=True)
    (target / "plugin.yaml").write_text("name: session-coord-native\n", encoding="utf-8")
    memory = home / "memories/MEMORY.md"
    memory.parent.mkdir()
    coord = home / "scripts/session_coord.py"
    coord.parent.mkdir()
    coord.write_bytes(b"board preserved\n")
    memory.write_text("operator before\n" + SETUP._native_block(coord) + "\noperator after\n", encoding="utf-8")
    (home / "state").mkdir()
    db = home / "state/session_coordination.db"
    db.write_bytes(b"user database opaque to installer")
    other = home / "profiles/other/config.yaml"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"user-owned: keep\n")
    return target, memory, coord, db, other


@pytest.mark.parametrize("problem", ["custom_block", "symlink", "carrier_parent", "wrong_plugin", "config"])
def test_preflight_refusal_has_no_mutations(tmp_path, problem):
    target, memory, coord, db, other = fixture(tmp_path)
    if problem == "custom_block":
        memory.write_text(memory.read_text().replace("STOP immediately", "custom operator rule"))
    elif problem == "symlink":
        moved = tmp_path / "redirected-plugin"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
    elif problem == "carrier_parent":
        moved = tmp_path / "shared-memory"
        memory.parent.rename(moved)
        memory.parent.symlink_to(moved, target_is_directory=True)
    elif problem == "wrong_plugin":
        (target / "plugin.yaml").write_text("name: unrelated\n")
    before = {p: p.read_bytes() for p in [memory, coord, db, other]}
    run = FaultRunner(tmp_path, problem)
    result = REMOVE.remove_profiles(run, ["fixture"], ["default"])
    assert result["ok"] is False
    assert result["mutation_performed"] is False
    assert target.exists()
    assert all(p.read_bytes() == content for p, content in before.items())
    assert not any(call[3:5] == ["plugins", "remove"] for call in run.calls)
    assert not (tmp_path / "state/session-coord-native-backups").exists()


@pytest.mark.parametrize("fault", ["disable", "remove"])
def test_partial_failure_preserves_board_and_a_recoverable_plugin(tmp_path, fault):
    target, memory, coord, db, other = fixture(tmp_path)
    before = {p: p.read_bytes() for p in [coord, db, other]}
    run = FaultRunner(tmp_path, fault)
    result = REMOVE.remove_profiles(run, ["fixture"], ["default"])
    assert result["ok"] is False
    assert result["profiles"][0]["status"] == "partial"
    assert target.exists()
    backup = Path(result["profiles"][0]["backup"])
    assert REMOVE._tree_signature(backup) == REMOVE._tree_signature(target)
    assert all(p.read_bytes() == content for p, content in before.items())
    if fault == "disable":
        assert SETUP.NATIVE_MARKER in memory.read_text()
    else:
        assert SETUP.NATIVE_MARKER not in memory.read_text()


def test_carrier_changed_after_preflight_is_not_overwritten(tmp_path):
    target, memory, *_ = fixture(tmp_path)
    run = FaultRunner(tmp_path)
    plan = REMOVE._plan_profile(run, ["fixture"], "default")
    memory.write_bytes(memory.read_bytes() + b"concurrent operator edit\n")
    changed = memory.read_bytes()
    with pytest.raises(ValueError, match="changed after preflight"):
        REMOVE._apply_profile(run, ["fixture"], plan, {"mutation_performed": False})
    assert memory.read_bytes() == changed
    assert target.exists()
    assert not (tmp_path / "state/session-coord-native-backups").exists()
