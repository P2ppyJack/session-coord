from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.isolated_subprocess_env import isolated_subprocess_env


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = importlib.util.spec_from_file_location("board_install_for_tests", INSTALLER)
assert SPEC and SPEC.loader
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)
BOARD_BEGIN = "<!-- BEGIN session-coord managed board-v2 -->"
BOARD_END = "<!-- END session-coord managed board-v2 -->"
BOT_BEGIN = "<!-- BEGIN session-coord managed bot-board-v2 -->"
BOT_END = "<!-- END session-coord managed bot-board-v2 -->"

RELEASED_WIRE = (
    "STANDING RULE — session-coord (wire v1): ALWAYS deconflict via the coordination board "
    "before touching shared resources (files, skills, memory, cron store, remote boxes, "
    "desktop UI) — any concurrent session/bot/cron may be mid-task. First: `python3 {sc} "
    "status`. Then register once (`ID=$(python3 {sc} register --task '...')`); claim before "
    "writes (`python3 {sc} claim --id $ID --res <keys> --wait`); `python3 {sc} done --id $ID` "
    "at task end. Full protocol: skill multi-session-coordination. Off-switch: `python3 {sc} "
    "disable`."
)

NATIVE_WIRE = (
    "STANDING RULE — session-coord (wire v1): ALWAYS deconflict via the coordination board "
    "before touching shared resources (files, skills, memory, cron store, remote boxes, "
    "desktop UI) — any concurrent session/bot/cron may be mid-task. First: `python3 {sc} "
    "status`. Then register once with trustworthy native identity (actual HERMES_SESSION_ID/"
    "HERMES_HOME auto-capture when present; otherwise explicit native/transport/target flags). "
    "Create an existing absolute checkpoint, then claim before writes with repeated resources "
    "(`python3 {sc} claim --id $ID --res <key> --res <key2> --yield --checkpoint /absolute/"
    "file`); exit 75 means STOP immediately, no polling or mutation. Resume only from the "
    "targeted `python3 {sc} continue --id BOARD --event EVENT`; use --wait only for legacy "
    "shell actors; `python3 {sc} done --id $ID` at task end. Full protocol: skill multi-session-"
    "coordination, references/automatic-resume.md. Off-switch: `python3 {sc} disable`."
)


def _command(home: Path, memory: Path, dest: Path, state: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        str(INSTALLER),
        "--dest",
        str(dest),
        "--state-dir",
        str(state),
        "--memory-file",
        str(memory),
        "--profiles-dir",
        str(home / "profiles"),
        "--skill-dest",
        str(home / "skill"),
        "--no-skill",
        "--no-wire-bots",
        "--no-wire-profiles",
        "--no-verify",
        *extra,
    ]


def _run(home: Path, memory: Path, dest: Path, state: Path, *extra: str):
    env = isolated_subprocess_env(home, hermes_home=home)
    return subprocess.run(
        _command(home, memory, dest, state, *extra),
        env=env,
        input="",
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )


def _run_bot(home: Path, dest: Path, state: Path, profiles: Path):
    env = isolated_subprocess_env(home, hermes_home=home)
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--dest",
            str(dest),
            "--state-dir",
            str(state),
            "--profiles-dir",
            str(profiles),
            "--no-skill",
            "--no-wire-memory",
            "--no-wire-profiles",
            "--no-verify",
        ],
        env=env,
        input="",
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("legacy_template", [RELEASED_WIRE, NATIVE_WIRE])
def test_installer_migrates_exact_legacy_memory_and_preserves_surrounding(
    tmp_path: Path, legacy_template: str
):
    home = tmp_path / "home with spaces"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    old_sc = tmp_path / "old location" / "session_coord.py"
    before = "personality before\n§\n" + legacy_template.format(sc=old_sc) + "\n§\nmemory after\n"
    memory.write_bytes(before.encode("utf-8"))
    mode = memory.stat().st_mode
    dest = tmp_path / "new scripts $() `tick`; safe"
    state = tmp_path / "state"

    result = _run(home, memory, dest, state)

    assert result.returncode == 0, result.stdout + result.stderr
    content = memory.read_bytes().decode("utf-8")
    assert content.startswith("personality before\n§\n")
    assert content.endswith("\n§\nmemory after\n")
    assert content.count(BOARD_BEGIN) == 1
    assert content.count(BOARD_END) == 1
    assert "session-coord (wire v1)" not in content
    assert "--yield" not in content
    assert "continue --id" not in content
    assert str(dest / "session_coord.py") in content
    assert f"python3 '{dest / 'session_coord.py'}' status" in content
    assert "ID=$(python3" in content
    backups = list(memory.parent.glob("MEMORY.md.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before.encode("utf-8")
    assert memory.stat().st_mode == mode


def test_installer_rerun_is_byte_identical_and_creates_no_second_backup(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    dest = tmp_path / "scripts"
    state = tmp_path / "state"

    first = _run(home, memory, dest, state)
    assert first.returncode == 0, first.stdout + first.stderr
    before_hash = _sha256(memory)
    before_backups = sorted(memory.parent.glob("MEMORY.md.bak-*"))

    second = _run(home, memory, dest, state)

    assert second.returncode == 0, second.stdout + second.stderr
    assert _sha256(memory) == before_hash
    assert sorted(memory.parent.glob("MEMORY.md.bak-*")) == before_backups
    assert "already current" in second.stdout


def test_custom_legacy_memory_is_preserved_and_reported_action_needed(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    custom = (
        "keep me\n§\nSTANDING RULE — session-coord (wire v1): custom operator policy; "
        "do not replace.\n§\nkeep me too\n"
    )
    memory.write_bytes(custom.encode("utf-8"))
    dest = tmp_path / "scripts"
    state = tmp_path / "state"

    result = _run(home, memory, dest, state)

    assert result.returncode == 1
    assert "ACTION NEEDED" in result.stdout
    assert memory.read_bytes() == custom.encode("utf-8")
    assert not list(memory.parent.glob("MEMORY.md.bak-*"))


def test_non_utf8_memory_is_preserved_and_reported_action_needed(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    original = b"operator bytes\xff\xfe"
    memory.write_bytes(original)

    result = _run(home, memory, tmp_path / "scripts", tmp_path / "state")

    assert result.returncode == 1
    assert "ACTION NEEDED" in result.stdout
    assert memory.read_bytes() == original
    assert not list(memory.parent.glob("MEMORY.md.bak-*"))


def test_crlf_legacy_memory_migrates_without_mixed_newlines(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    coord = (tmp_path / "scripts" / "session_coord.py").resolve()
    legacy = RELEASED_WIRE.format(sc=coord).replace("\n", "\r\n")
    original = ("before\r\n§\r\n" + legacy + "\r\n§\r\nafter\r\n").encode()
    memory.write_bytes(original)

    result = _run(home, memory, tmp_path / "scripts", tmp_path / "state")

    assert result.returncode == 0, result.stdout + result.stderr
    content = memory.read_bytes()
    assert b"\n" not in content.replace(b"\r\n", b"")
    assert BOARD_BEGIN.encode() in content
    assert b"session-coord (wire v1)" not in content
    backups = list(memory.parent.glob("MEMORY.md.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_append_to_crlf_memory_preserves_newline_style(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_bytes("entry one\r\n§\r\nentry two\r\n".encode())

    result = _run(home, memory, tmp_path / "scripts", tmp_path / "state")

    assert result.returncode == 0, result.stdout + result.stderr
    content = memory.read_bytes()
    assert b"\n" not in content.replace(b"\r\n", b"")
    assert BOARD_BEGIN.encode() in content


def test_custom_managed_memory_block_is_preserved_as_action_needed(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    original = (
        "operator preface\n§\n"
        f"{BOARD_BEGIN}\ncustomized operator text\n"
        f"{BOARD_END}\n"
    ).encode()
    memory.write_bytes(original)

    result = _run(home, memory, tmp_path / "scripts", tmp_path / "state")

    assert result.returncode == 1
    assert "ACTION NEEDED" in result.stdout
    assert memory.read_bytes() == original
    assert not list(memory.parent.glob("MEMORY.md.bak-*"))


def test_reversed_managed_markers_are_preserved_not_crashed(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    original = (f"{BOARD_END}\ncustom\n{BOARD_BEGIN}\n").encode()
    memory.write_bytes(original)

    result = _run(
        home, memory, tmp_path / "scripts", tmp_path / "state", "--no-verify"
    )

    assert result.returncode == 1
    assert "ACTION NEEDED" in result.stdout
    assert memory.read_bytes() == original


def test_current_managed_plus_legacy_candidate_is_ambiguous(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    coord = tmp_path / "scripts" / "session_coord.py"
    original = (
        INSTALL.board_memory_block(coord).rstrip("\n")
        + "\n§\n"
        + RELEASED_WIRE.format(sc=coord)
    ).encode()
    memory.write_bytes(original)

    result = _run(home, memory, coord.parent, tmp_path / "state", "--no-verify")

    assert result.returncode == 1
    assert "ACTION NEEDED" in result.stdout
    assert memory.read_bytes() == original


def test_missing_bash_is_reported_as_partial_verification(tmp_path: Path, monkeypatch):
    suite = tmp_path / "selftest_wakes.py"
    suite.write_text("print('wake suite green')\n", encoding="utf-8")
    monkeypatch.setattr(INSTALL, "find_bash", lambda: None)

    rc, complete = INSTALL.run_suites(tmp_path)

    assert rc == 0
    assert complete is False


@pytest.mark.parametrize(
    "fixture_name", ["released_bot_wire_v1.md", "native_candidate_bot_wire_v1.md"]
)
def test_installer_migrates_exact_legacy_bot_blurbs_without_touching_personality(
    tmp_path: Path, fixture_name: str
):
    home = tmp_path / "home"
    profiles = home / "profiles"
    soul = profiles / "scout" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    legacy = (FIXTURES / fixture_name).read_text(encoding="utf-8").replace(
        "<botname>", "scout"
    )
    before = "# Scout\nKeep this persona.\n\n" + legacy + "\nKeep this footer.\n"
    soul.write_bytes(before.encode("utf-8"))

    result = _run_bot(home, tmp_path / "scripts", tmp_path / "state", profiles)

    assert result.returncode == 0, result.stdout + result.stderr
    content = soul.read_bytes().decode("utf-8")
    assert content.startswith("# Scout\nKeep this persona.\n\n")
    assert content.endswith("\nKeep this footer.\n")
    assert content.count(BOT_BEGIN) == 1
    assert content.count(BOT_END) == 1
    assert "session-coord (bot-wire v1)" not in content
    assert "--yield" not in content
    assert "bot:scout" in content
    backups = list(soul.parent.glob("SOUL.md.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before.encode("utf-8")


def test_custom_bot_marker_is_preserved_and_reported_action_needed(tmp_path: Path):
    home = tmp_path / "home"
    profiles = home / "profiles"
    soul = profiles / "scout" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    custom = b"# Scout\nCustom session-coord (bot-wire v1) instructions.\n"
    soul.write_bytes(custom)

    result = _run_bot(home, tmp_path / "scripts", tmp_path / "state", profiles)

    assert result.returncode == 1
    assert "ACTION NEEDED" in result.stdout
    assert soul.read_bytes() == custom
    assert not list(soul.parent.glob("SOUL.md.bak-*"))


def test_check_is_read_only_and_honors_wiring_opt_outs(tmp_path: Path):
    home = tmp_path / "home"
    memory = home / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    custom = "session-coord (wire v1): customized\n"
    memory.write_bytes(custom.encode("utf-8"))
    protected = home / "protected.bin"
    protected.write_bytes(b"protected\x00payload")
    before = {path: _sha256(path) for path in (memory, protected)}
    dest = tmp_path / "scripts"
    state = tmp_path / "state"

    result = _run(
        home,
        memory,
        dest,
        state,
        "--check",
        "--no-wire-memory",
        "--no-wire-bots",
        "--no-wire-profiles",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped (--no-wire-memory)" in result.stdout
    assert {path: _sha256(path) for path in (memory, protected)} == before
    assert not dest.exists()
    assert not state.exists()
    assert not list(memory.parent.glob("*.bak-*"))
