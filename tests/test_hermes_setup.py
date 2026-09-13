from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.isolated_subprocess_env import isolated_subprocess_env


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "hermes_setup.py"
INSTALLER = ROOT / "install.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("session_coord_installer", INSTALLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALL = _load_installer()


STUB = r'''#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path

state_path = Path(os.environ["HERMES_STUB_STATE"])
log_path = Path(os.environ["HERMES_STUB_LOG"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

if len(args) < 3 or args[0] != "-p":
    print("profile required", file=sys.stderr); raise SystemExit(2)
profile = args[1]
cmd = args[2:]
record = state["profiles"].get(profile)
if record is None:
    print("unknown profile", file=sys.stderr); raise SystemExit(2)
home = Path(record["home"])

if cmd == ["config", "path"]:
    print(home / "config.yaml"); raise SystemExit(0)
if cmd[:2] == ["plugins", "doctor"]:
    target = Path(cmd[2])
    if not record.get("doctor", True):
        print("native registrar unavailable", file=sys.stderr); raise SystemExit(1)
    if target.name == "session-coord-native" and not record.get("installed", False):
        print("not installed", file=sys.stderr); raise SystemExit(1)
    print("doctor ok"); raise SystemExit(0)
if cmd[:3] == ["plugins", "list", "--json"]:
    source = "git pinned@" + state["source_sha"][:8]
    rows = []
    if record.get("installed", False):
        rows.append({"name": "session-coord-native", "status": "enabled" if record.get("enabled") else "disabled", "version": "0.1.0", "description": "fixture", "source": source, "removed": None})
    print(json.dumps(rows)); raise SystemExit(0)
if cmd[:2] == ["plugins", "install"]:
    if record.get("install_fail"):
        print("install failed", file=sys.stderr); raise SystemExit(1)
    record["installed"] = True
    record["enabled"] = False
    installed = home / "plugins" / "session-coord-native"
    source = Path(state["plugin_path"])
    if installed.exists():
        shutil.rmtree(installed)
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, installed)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print("installed"); raise SystemExit(0)
if cmd[:3] == ["plugins", "enable", "session-coord-native"]:
    if record.get("enable_fail"):
        print("enable failed", file=sys.stderr); raise SystemExit(1)
    record["enabled"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print("enabled"); raise SystemExit(0)
if cmd == ["config", "get", "plugins", "--json"]:
    if record.get("plugins_missing") and not record.get("enabled"):
        print("Config key not set: plugins", file=sys.stderr); raise SystemExit(2)
    value = {"enabled": ["session-coord-native"] if record.get("enabled") else [], "disabled": [] if record.get("enabled") else ["session-coord-native"]}
    print(json.dumps(value)); raise SystemExit(0)
if cmd == ["config", "set", "delegation.wait_for_all", "true"]:
    if record.get("set_fail"):
        print("set failed", file=sys.stderr); raise SystemExit(1)
    record["wait"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print("set"); raise SystemExit(0)
if cmd == ["config", "get", "delegation.wait_for_all", "--json"]:
    if record.get("bad_wait_readback"):
        print(json.dumps("true")); raise SystemExit(0)
    print(json.dumps(bool(record.get("wait", False)))); raise SystemExit(0)
if cmd == ["session-coord", "native-check", "--json"]:
    record["native_checks"] = int(record.get("native_checks", 0)) + 1
    if record.get("mutate_carrier") and record["native_checks"] == 2:
        carrier = home / "memories" / "MEMORY.md"
        carrier.write_text(carrier.read_text(encoding="utf-8") + "\noperator race\n", encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if record.get("native_malformed"):
        print("{not-json"); raise SystemExit(0)
    if not (record.get("installed") and record.get("enabled")):
        print("command unavailable", file=sys.stderr); raise SystemExit(2)
    wait_supported = not record.get("wait_unsupported", False)
    payload = {"supported": True, "surfaces": ["cli", "tui", "gateway"], "wait_for_all_supported": wait_supported, "wait_for_all": bool(record.get("wait")) if wait_supported else None, "activation": "fresh_process_only", "reason": "" if wait_supported else "policy helper unavailable"}
    print(json.dumps(payload)); raise SystemExit(0)
if cmd[:4] == ["session-coord", "watchdog-setup", "--json", "--check"]:
    if state.get("watchdog_empty"):
        print("{}"); raise SystemExit(0)
    ok = bool(state.get("watchdog"))
    print(json.dumps({"ok": ok, "status": "configured" if ok else "absent"}))
    raise SystemExit(0 if ok else 1)
if cmd == ["session-coord", "watchdog-setup", "--json"]:
    if state.get("watchdog_fail"):
        print(json.dumps({"ok": False, "status": "partial", "recovery": "run --check"})); raise SystemExit(1)
    state["watchdog"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print(json.dumps({"ok": True, "status": "configured"})); raise SystemExit(0)
print("unsupported: " + repr(cmd), file=sys.stderr)
raise SystemExit(2)
'''


def _git_plugin(tmp_path: Path) -> tuple[Path, str]:
    plugin = tmp_path / "plugin source; literal $()"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: session-coord-native\nversion: 0.1.0\ndescription: fixture\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text("def register(ctx):\n    return None\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(plugin)], check=True)
    subprocess.run(["git", "-C", str(plugin), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(plugin), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(plugin), "add", "."], check=True)
    subprocess.run(["git", "-C", str(plugin), "commit", "-qm", "fixture"], check=True)
    sha = subprocess.check_output(["git", "-C", str(plugin), "rev-parse", "HEAD"], text=True).strip()
    return plugin, sha


def _write_board(home: Path, coord: Path) -> Path:
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("operator note\n§\n" + INSTALL.board_memory_block(coord), encoding="utf-8")
    return memory


def _fixture(tmp_path: Path, profiles: tuple[str, ...] = ("default",)):
    root = tmp_path / "Hermes home $() ; spaces"
    coord = root / "scripts" / "session_coord.py"
    coord.parent.mkdir(parents=True)
    coord.write_text("# fixture\n", encoding="utf-8")
    records = {}
    for profile in profiles:
        home = root if profile == "default" else root / "profiles" / profile
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("# untouched\n", encoding="utf-8")
        _write_board(home, coord)
        records[profile] = {"home": str(home), "doctor": True, "installed": False, "enabled": False, "wait": False}
    plugin, sha = _git_plugin(tmp_path)
    state = tmp_path / "stub-state.json"
    state.write_text(
        json.dumps(
            {
                "profiles": records,
                "source_sha": sha,
                "plugin_path": str(plugin),
                "watchdog": False,
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "argv.jsonl"
    stub = tmp_path / "hermes stub.py"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    return root, coord, plugin, state, log, stub


def _run(root: Path, coord: Path, plugin: Path, state: Path, log: Path, stub: Path, *args: str):
    env = isolated_subprocess_env(
        root,
        extra={"HERMES_STUB_STATE": state, "HERMES_STUB_LOG": log},
    )
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            *args,
            "--hermes",
            sys.executable,
            "--hermes-arg",
            str(stub),
            "--plugin-path",
            str(plugin),
            "--json",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def test_check_is_read_only_and_reports_setup_needed(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    before = _tree_hashes(root)

    result = _run(root, coord, plugin, state, log, stub, "check", "--profile", "default")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["mode"] == "check"
    assert payload["ok"] is False
    assert payload["profiles"][0]["status"] == "setup_required"
    assert _tree_hashes(root) == before
    assert not any(call[2:4] in (["plugins", "install"], ["plugins", "enable"]) for call in _calls(log))
    assert not any(call[2:4] == ["config", "set"] for call in _calls(log))


def test_wrong_plugin_identity_is_rejected_before_hermes_calls(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    (plugin / "plugin.yaml").write_text("name: unrelated-plugin\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(plugin), "add", "plugin.yaml"], check=True)
    subprocess.run(["git", "-C", str(plugin), "commit", "-m", "wrong identity"], check=True, capture_output=True)

    result = _run(root, coord, plugin, state, log, stub, "check", "--profile", "default")

    assert result.returncode == 1
    assert "expected 'session-coord-native'" in json.loads(result.stdout)["reason"]
    assert _calls(log) == []


def test_dirty_source_still_runs_doctor_but_never_installs(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    (plugin / "working-tree-change.py").write_text("x = 1\n", encoding="utf-8")
    before = _tree_hashes(root)

    result = _run(root, coord, plugin, state, log, stub, "check", "--profile", "default")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["source"]["clean"] is False
    assert payload["profiles"][0]["status"] == "source_snapshot_required"
    assert _tree_hashes(root) == before
    calls = _calls(log)
    assert any(call[2:4] == ["plugins", "doctor"] for call in calls)
    assert not any(call[2:4] == ["plugins", "install"] for call in calls)


def test_setup_configures_two_profiles_then_writes_native_blocks(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path, ("default", "build_bot"))

    result = _run(
        root, coord, plugin, state, log, stub,
        "setup", "--profile", "default", "--profile", "build_bot",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["restart_required"] is True
    assert [item["profile"] for item in payload["profiles"]] == ["default", "build_bot"]
    for profile in payload["profiles"]:
        assert profile["status"] == "configured_restart_required"
        assert profile["wait_for_all"] is True
        assert profile["native_enrollment"] == "current"
    for home in (root, root / "profiles" / "build_bot"):
        text = (home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
        assert text.count("<!-- BEGIN session-coord managed hermes-native-v1 -->") == 1
        assert "--yield" in text
        assert f"python3 '{coord}' claim" in text
    install_calls = [call for call in _calls(log) if call[2:4] == ["plugins", "install"]]
    assert len(install_calls) == 2
    assert all(call[4].startswith("file://") for call in install_calls)
    assert all(str(plugin) not in call[4] for call in install_calls)  # URI escaped, one argv token


def test_custom_native_block_is_preserved_before_any_mutation(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    memory = root / "memories" / "MEMORY.md"
    original = memory.read_text(encoding="utf-8") + (
        "\n§\n<!-- BEGIN session-coord managed hermes-native-v1 -->\n"
        "custom operator native policy\n"
        "<!-- END session-coord managed hermes-native-v1 -->"
    )
    memory.write_text(original, encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")

    assert result.returncode == 1
    assert json.loads(result.stdout)["profiles"][0]["status"] == "action_needed"
    assert memory.read_text(encoding="utf-8") == original
    assert not any(
        call[2:4] in (["plugins", "install"], ["plugins", "enable"], ["config", "set"])
        for call in _calls(log)
    )


def test_setup_treats_missing_plugins_config_as_disabled_not_malformed(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    data = json.loads(state.read_text(encoding="utf-8"))
    data["profiles"]["default"]["plugins_missing"] = True
    state.write_text(json.dumps(data), encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["profiles"][0]["plugin"] == "enabled"


def test_preflight_failure_in_second_profile_prevents_all_mutation(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path, ("default", "broken"))
    data = json.loads(state.read_text(encoding="utf-8"))
    data["profiles"]["broken"]["doctor"] = False
    state.write_text(json.dumps(data), encoding="utf-8")
    before = _tree_hashes(root)

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default", "--profile", "broken")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["profiles"][1]["status"] == "unsupported_host"
    assert _tree_hashes(root) == before
    assert not any(call[2:4] in (["plugins", "install"], ["plugins", "enable"], ["config", "set"]) for call in _calls(log))


def test_carrier_change_after_preflight_is_preserved_without_native_write(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    data = json.loads(state.read_text(encoding="utf-8"))
    data["profiles"]["default"]["mutate_carrier"] = True
    state.write_text(json.dumps(data), encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["profiles"][0]["status"] == "partial"
    memory = root / "memories" / "MEMORY.md"
    text = memory.read_text(encoding="utf-8")
    assert "operator race" in text
    assert "managed hermes-native-v1" not in text
    assert not list(memory.parent.glob("MEMORY.md.bak-*"))


def test_malformed_native_json_after_install_reports_partial_without_enrollment(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    data = json.loads(state.read_text(encoding="utf-8"))
    data["profiles"]["default"]["native_malformed"] = True
    state.write_text(json.dumps(data), encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["profiles"][0]["status"] == "partial"
    assert "malformed" in payload["profiles"][0]["reason"].lower()
    text = (root / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert "managed hermes-native-v1" not in text


def test_non_boolean_config_readback_never_writes_native_enrollment(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    data = json.loads(state.read_text(encoding="utf-8"))
    data["profiles"]["default"]["bad_wait_readback"] = True
    state.write_text(json.dumps(data), encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["profiles"][0]["status"] == "partial"
    assert "boolean" in payload["profiles"][0]["reason"].lower()
    assert "managed hermes-native-v1" not in (root / "memories" / "MEMORY.md").read_text(encoding="utf-8")


def test_successful_rerun_is_idempotent(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    first = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")
    assert first.returncode == 0, first.stdout + first.stderr
    memory = root / "memories" / "MEMORY.md"
    first_hash = hashlib.sha256(memory.read_bytes()).hexdigest()
    backups = sorted(memory.parent.glob("MEMORY.md.bak-*"))
    log.write_text("", encoding="utf-8")

    second = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")

    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)["mutation_performed"] is False
    assert hashlib.sha256(memory.read_bytes()).hexdigest() == first_hash
    assert sorted(memory.parent.glob("MEMORY.md.bak-*")) == backups
    mutating = [call for call in _calls(log) if call[2:4] in (["plugins", "install"], ["plugins", "enable"], ["config", "set"])]
    assert mutating == []

    checked = _run(root, coord, plugin, state, log, stub, "check", "--profile", "default")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["mutation_performed"] is False


def test_bot_native_enrollment_uses_soul_without_memory_separator(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path, ("default", "scout"))
    scout = root / "profiles" / "scout"
    (scout / "memories" / "MEMORY.md").unlink()
    soul = scout / "SOUL.md"
    soul.write_text("# Scout\n\n" + INSTALL._bot_blurb("scout", coord), encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "scout")

    assert result.returncode == 0, result.stdout + result.stderr
    content = soul.read_text(encoding="utf-8")
    assert "managed bot-board-v2" in content
    assert "managed hermes-native-v1" in content
    assert "\n§\n" not in content
    assert not (scout / "memories" / "MEMORY.md").exists()


def test_watchdog_is_one_explicit_default_profile_operation(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)

    result = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default", "--watchdog")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["watchdog"]["status"] == "configured"
    calls = _calls(log)
    setup_calls = [call for call in calls if call == ["-p", "default", "session-coord", "watchdog-setup", "--json"]]
    check_calls = [call for call in calls if call == ["-p", "default", "session-coord", "watchdog-setup", "--json", "--check"]]
    assert len(setup_calls) == 1
    assert len(check_calls) >= 1
    assert not any(call[1] != "default" and "watchdog-setup" in call for call in calls)


def test_watchdog_empty_json_is_not_treated_as_configured(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    first = _run(root, coord, plugin, state, log, stub, "setup", "--profile", "default")
    assert first.returncode == 0, first.stdout + first.stderr
    data = json.loads(state.read_text(encoding="utf-8"))
    data["watchdog_empty"] = True
    state.write_text(json.dumps(data), encoding="utf-8")
    log.write_text("", encoding="utf-8")

    result = _run(root, coord, plugin, state, log, stub, "check", "--profile", "default", "--watchdog")

    assert result.returncode == 1
    assert json.loads(result.stdout)["watchdog"]["status"] == "setup_required"
    assert not any(
        call == ["-p", "default", "session-coord", "watchdog-setup", "--json"]
        for call in _calls(log)
    )


def test_all_profiles_selects_only_real_profile_directories(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path, ("default", "alpha", "zeta"))
    unrelated = root / "profiles" / ".hidden"
    unrelated.mkdir(parents=True)
    protected = root / "unselected.bin"
    protected.write_bytes(b"unchanged")
    before = hashlib.sha256(protected.read_bytes()).hexdigest()

    result = _run(root, coord, plugin, state, log, stub, "setup", "--all-profiles")

    assert result.returncode == 0, result.stdout + result.stderr
    assert [item["profile"] for item in json.loads(result.stdout)["profiles"]] == ["default", "alpha", "zeta"]
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == before


def test_argument_errors_make_no_runner_calls(tmp_path: Path):
    root, coord, plugin, state, log, stub = _fixture(tmp_path)
    result = _run(root, coord, plugin, state, log, stub, "setup")
    assert result.returncode == 2
    assert _calls(log) == []
