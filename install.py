#!/usr/bin/env python3
"""install.py — set up (or upgrade) session-coord on this machine.

Pure standard library, no network, cross-platform (Linux / macOS / Windows,
Python >= 3.8). Safe to re-run: it UPGRADES an existing install in place and
never touches your live board data.

What it does
------------
1. Copies the CLI + wake-state sibling + cron guard + script-only resume
   watchdog + selftests into a scripts directory
   (default: ~/.hermes/scripts, override with --dest or $HERMES_SCRIPTS_DIR).
2. Creates the state directory the board and master switch live in
   (default: ~/.hermes/state, override with --state-dir or $HERMES_STATE_DIR).
3. NEVER clobbers your data: the board DB (session_coordination.db) and an
   existing cron manifest (cron_resources.json) are left exactly as they are.
   Any script it replaces whose contents differ is backed up to
   <name>.bak-<timestamp> first, so a local modification is never lost silently.
4. Verifies the install by running the bundled selftest suites (skip with
   --no-verify). The wake suite uses native Python; the four legacy suites use
   a real bash and are skipped, with an explicit notice, when none is available.
5. Wires the agent in: installs the canonical managed board-v2 rule
   ("always consult the coordination board") in the agent memory store (default:
   <HERMES_HOME|~/.hermes>/memories/MEMORY.md; --memory-file overrides;
   --no-wire-memory skips). Exact shipped wire-v1 blocks are migrated;
   customized/ambiguous text is preserved as ACTION NEEDED. Idempotent,
   backed up (.bak-<ts>) before any change, and fail-open: an absent or
   unwritable store prints the entry for manual placement and never fails
   the install.
6. Installs the skill bundle: copies SKILL.md + references/ + templates/ +
   examples/ from skills/multi-session-coordination/ into
   <HERMES_HOME|~/.hermes>/skills/multi-session-coordination (--skill-dest
   overrides; --no-skill skips), so the skill is loadable by the agent
   exactly like a `hermes skills install` copy — idempotent, backs up a
   locally-modified file before replacing it, and never deletes anything.
   (scripts/ are NOT duplicated into the skill dir — they already land in
   the scripts directory by step 1.)
7. Wires existing BOT profiles with the managed bot-board-v2 SOUL.md block.
   Exact shipped bot-wire-v1 variants are migrated; customized/ambiguous text
   is preserved as ACTION NEEDED. `--profiles-dir` overrides and
   `--no-wire-bots` skips. Changes are backed up; profiles with no SOUL.md are
   reported, never invented (they are step 8's job instead).
8. Wires NON-BOT profiles in: a profile is a full agent instance with its
   OWN memory store the main store's rule never reaches, so the standing
   rule is appended to each SOUL-less profile's
   <profiles>/<name>/memories/MEMORY.md (created if absent;
   --no-wire-profiles skips). Bot profiles are skipped here — the blurb is
   their carrier. Profiles/bots created after this install need wiring too:
   re-run the installer; `session_coord.py status` flags unenrolled ones.

Nothing runs in the background; there is no daemon and no uninstall step beyond
deleting the copied files (your data dir is yours).
"""

from __future__ import annotations

import argparse
import contextlib
import filecmp
import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess  # nosec B404 -- only used to run the bundled selftests; opt out via --no-verify
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE / "skills" / "multi-session-coordination"
SRC = SKILL_DIR / "scripts"
EXAMPLES = SKILL_DIR / "examples"

# Skill-bundle files (everything the agent needs to load the skill, mirroring
# what `hermes skills install` copies). scripts/ is deliberately excluded: the
# payload above already installs those into the scripts dir, and the skill
# documents the scripts dir as the tool's canonical location.
SKILL_BUNDLE = (
    ("SKILL.md", False),
    ("references", False),
    ("templates", False),
    ("examples", False),
)

# name -> executable?  (files copied into the scripts dir)
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
SUITES = ["selftest.sh", "selftest_priority.sh", "selftest_cron.sh", "selftest_toggle.sh"]
PYTHON_SUITES = ["selftest_wakes.py"]

# Data files this installer must NEVER overwrite (they hold live state).
PROTECTED = ("session_coordination.db", "cron_resources.json")

# Legacy enrollment markers. They identify candidates only; marker presence
# alone is never proof that a customized instruction is safe to replace.
WIRE_MARKER = "session-coord (wire v1)"

# Legacy bot marker. It identifies a migration candidate only; the status audit
# requires the exact current managed block rather than this substring.
BOT_WIRE_MARKER = "session-coord (bot-wire v1)"
BOT_BLURB_TEMPLATE = SKILL_DIR / "templates" / "bot-soul-coordination.md"

# Managed enrollment markers are versioned independently from the CLI and DB.
# Exact begin/end pairs make upgrades surgical and let customized legacy text
# fail closed instead of being silently counted as current.
BOARD_WIRE_BEGIN = "<!-- BEGIN session-coord managed board-v2 -->"
BOARD_WIRE_END = "<!-- END session-coord managed board-v2 -->"
BOT_BOARD_WIRE_BEGIN = "<!-- BEGIN session-coord managed bot-board-v2 -->"
BOT_BOARD_WIRE_END = "<!-- END session-coord managed bot-board-v2 -->"
LEGACY_BOT_WIRE_HASHES = {
    "4565651a35c2e225e65fbb0508824425c6a52b3942b0b9529041d4a3557a9cf2",
    "03118d892328c5d0bf7b8e0f741a3b96fcf7bda2d30efc42528f3d7301ec9c91",
}
LEGACY_BOT_HEADING = "## Shared-resource coordination (Hermes co-worker protocol)"
LEGACY_BOT_END = "audit; do not remove this line."

# Exact public variants previously written by released v2.4.0 and by the
# pre-release native-continuation candidate. The path placeholder is matched
# consistently across every occurrence, so arbitrary lookalikes are preserved.
LEGACY_WIRE_ENTRY = (
    "STANDING RULE — session-coord (wire v1): ALWAYS deconflict via the "
    "coordination board before touching shared resources (files, skills, "
    "memory, cron store, remote boxes, desktop UI) — any concurrent "
    "session/bot/cron may be mid-task. First: `python3 {sc} status`. Then "
    "register once (`ID=$(python3 {sc} register --task '...')`); claim "
    "before writes (`python3 {sc} claim --id $ID --res <keys> --wait`); "
    "`python3 {sc} done --id $ID` at task end. Full protocol: skill "
    "multi-session-coordination. Off-switch: `python3 {sc} disable`."
)
NATIVE_CANDIDATE_WIRE_ENTRY = (
    "STANDING RULE — session-coord (wire v1): ALWAYS deconflict via the "
    "coordination board before touching shared resources (files, skills, "
    "memory, cron store, remote boxes, desktop UI) — any concurrent "
    "session/bot/cron may be mid-task. First: `python3 {sc} status`. Then "
    "register once with trustworthy native identity (actual HERMES_SESSION_ID/"
    "HERMES_HOME auto-capture when present; otherwise explicit native/transport/"
    "target flags). Create an existing absolute checkpoint, then claim before "
    "writes with repeated resources (`python3 {sc} claim --id $ID --res <key> "
    "--res <key2> --yield --checkpoint /absolute/file`); exit 75 means STOP "
    "immediately, no polling or mutation. Resume only from the targeted "
    "`python3 {sc} continue --id BOARD --event EVENT`; use --wait only for "
    "legacy shell actors; `python3 {sc} done --id $ID` at task end. Full "
    "protocol: skill multi-session-coordination, references/automatic-resume.md. "
    "Off-switch: `python3 {sc} disable`."
)

# Board-only enrollment remains useful without Hermes, a plugin, a daemon, or
# an automatic-resume consumer. Native --yield instructions live in a separate
# opt-in block written by hermes_setup.py only after capability/config proof.
BOARD_WIRE_TEMPLATE = (
    BOARD_WIRE_BEGIN + "\n"
    "STANDING RULE — session-coord (board-wire v2): Before mutating shared "
    "resources (files, skills, memory, cron store, remote boxes, or desktop UI), "
    "first run `python3 {sc} status`. Register once per task with "
    "`ID=$(python3 {sc} register --task '...' --surface <surface>)`, then "
    "atomically claim every required resource with `python3 {sc} claim --id $ID "
    "--res <key> [--res <key2>]`. Continue only after CLAIMED; when HELD/QUEUED, "
    "do not mutate the requested resources—use `check`, `inbox`, or one bounded "
    "`--wait` shell call and wait for release. Run `python3 {sc} done --id $ID` "
    "at task end. Priorities are user-set; never `steal` without explicit approval. "
    "Full protocol: skill multi-session-coordination. Off-switch: "
    "`python3 {sc} disable`.\n" + BOARD_WIRE_END
)


def _template_pattern(template: str) -> re.Pattern:
    """Compile an exact template whose repeated ``{sc}`` values must agree."""
    parts = template.split("{sc}")
    pattern = re.escape(parts[0])
    for index, part in enumerate(parts[1:]):
        value = r"(?P<sc>[^\r\n]+?)" if index == 0 else r"(?P=sc)"
        pattern += value + re.escape(part)
    return re.compile(pattern)


def _line_sep(content: str) -> str:
    """Use the file's existing newline convention for inserted managed text."""
    return "\r\n" if "\r\n" in content else "\n"


def board_memory_block(sc: Path, *, newline: str = "\n") -> str:
    """Render the board-only managed memory block for one installed CLI path."""
    rendered = BOARD_WIRE_TEMPLATE.format(sc=shlex.quote(str(sc)))
    return rendered.replace("\n", newline)


def _classify_board_memory(content: str, sc: Path) -> tuple:
    """Return ``(state, replacement)`` without mutating an enrollment carrier."""
    begin_count = content.count(BOARD_WIRE_BEGIN)
    end_count = content.count(BOARD_WIRE_END)
    if begin_count or end_count:
        if begin_count != 1 or end_count != 1:
            return "ambiguous", content
        begin = content.index(BOARD_WIRE_BEGIN)
        end_start = content.index(BOARD_WIRE_END)
        if end_start < begin:
            return "ambiguous", content
        end = end_start + len(BOARD_WIRE_END)
        if WIRE_MARKER in (content[:begin] + content[end:]):
            return "ambiguous", content
        block = content[begin:end]
        normalized = block.replace("\r\n", "\n")
        if _template_pattern(BOARD_WIRE_TEMPLATE).fullmatch(normalized) is None:
            return "ambiguous", content
        wanted = board_memory_block(sc, newline=_line_sep(content))
        if block == wanted:
            return "current", content
        return "upgrade", content[:begin] + wanted + content[end:]

    matches = []
    for template in (LEGACY_WIRE_ENTRY, NATIVE_CANDIDATE_WIRE_ENTRY):
        matches.extend(match.span() for match in _template_pattern(template).finditer(content))
    matches = sorted(set(matches))
    if len(matches) == 1 and content.count(WIRE_MARKER) == 1:
        begin, end = matches[0]
        block = board_memory_block(sc, newline=_line_sep(content))
        return "upgrade", content[:begin] + block + content[end:]
    if WIRE_MARKER in content:
        return "ambiguous", content
    return "append", _memory_append_text(
        content, board_memory_block(sc, newline=_line_sep(content))
    )


def _memory_append_text(content: str, entry: str) -> str:
    """Append an entry using the carrier's LF or CRLF convention.

    Hermes entries are separated by ``§`` on its own line; stripping only
    trailing line terminators preserves all other content.
    """
    newline = _line_sep(content or entry)
    content = content.rstrip("\r\n")
    if not content:
        return entry
    return content + newline + "§" + newline + entry


def wire_memory(memory_file: Path, sc: Path, *, dry: bool) -> str:
    """Install or upgrade the board-only rule in an agent memory store.

    Exact shipped variants are replaced surgically. Customized, duplicated,
    or malformed candidates remain untouched and return ``ACTION NEEDED``.
    Every actual change to an existing file is backed up first.
    """
    try:
        if memory_file.exists():
            original = memory_file.read_bytes()
            content = original.decode("utf-8")
            state, replacement = _classify_board_memory(content, sc)
            if state == "current":
                return "wired (managed board-v2 already current)"
            if state == "ambiguous":
                return (
                    "ACTION NEEDED — customized or malformed coordination enrollment "
                    f"preserved at {memory_file}; compare it with "
                    "examples/memory-entry.example.md"
                )
            if dry:
                verb = (
                    "upgrade recognized enrollment in" if state == "upgrade" else "append rule to"
                )
                return f"would {verb} {memory_file}"
            bak = rewrite_carrier_if_unchanged(memory_file, original, replacement.encode("utf-8"))
            if bak is None:
                return (
                    "ACTION NEEDED — memory carrier changed during upgrade; "
                    f"preserved current bytes at {memory_file}"
                )
            verb = "upgraded recognized enrollment" if state == "upgrade" else "appended"
            return f"wired ({verb}; previous copy -> {bak.name})"
        if memory_file.parent.exists():
            if dry:
                return f"would create {memory_file} with the rule"
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            memory_file.write_bytes(board_memory_block(sc).encode("utf-8"))
            return f"wired (created {memory_file})"
    except UnicodeError as exc:
        return (
            "ACTION NEEDED — memory carrier is not valid UTF-8 and was preserved "
            f"at {memory_file}: {exc}"
        )
    except OSError as exc:
        return f"NOT WIRED — {type(exc).__name__}: {exc} (paste manually)"
    if dry:
        return f"no agent memory store found at {memory_file} (nothing to write)"
    return (
        f"NOT WIRED — no agent memory store at {memory_file}; paste the "
        "entry manually (examples/memory-entry.example.md)"
    )


def _bot_blurb(botname: str, sc: Path) -> str:
    """The SOUL.md enrollment block: everything in the template from the
    '## Shared-resource coordination' heading down (the prose above it is
    for humans reading the template, not for the bot's persona), with
    <botname> substituted. Raises OSError if the template is unreadable —
    callers treat that as fail-open."""
    text = BOT_BLURB_TEMPLATE.read_text(encoding="utf-8")
    idx = text.find(BOT_BOARD_WIRE_BEGIN)
    block = text[idx:] if idx >= 0 else text
    return (
        block.replace("<botname>", botname)
        .replace("<session_coord_path>", shlex.quote(str(sc)))
        .rstrip("\n")
        + "\n"
    )


def _normalized_bot_block(block: str, botname: str) -> str:
    """Normalize only installer-controlled bot/path placeholders for comparison."""
    normalized = block.replace("\r\n", "\n").replace(f"bot:{botname}", "bot:<botname>")
    return re.sub(r"^SC=.*$", "SC=<session_coord_path>", normalized, count=1, flags=re.MULTILINE)


def _legacy_bot_span(content: str, botname: str):
    """Return the exact recognized legacy bot-block span, or ``None``."""
    if content.count(BOT_WIRE_MARKER) != 1:
        return None
    marker = content.index(BOT_WIRE_MARKER)
    begin = content.rfind(LEGACY_BOT_HEADING, 0, marker)
    if begin < 0:
        return None
    end = content.find(LEGACY_BOT_END, marker)
    if end < 0:
        return None
    end += len(LEGACY_BOT_END)
    if content.startswith("\r\n", end):
        end += 2
    elif content.startswith("\n", end):
        end += 1
    candidate = content[begin:end].replace("\r\n", "\n")
    candidate = candidate.replace(f"bot:{botname}", "bot:<botname>").rstrip("\n") + "\n"
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return (begin, end) if digest in LEGACY_BOT_WIRE_HASHES else None


def _classify_bot_soul(content: str, botname: str, sc: Path) -> tuple:
    """Return ``(state, replacement)`` for one bot persona without writing."""
    begin_count = content.count(BOT_BOARD_WIRE_BEGIN)
    end_count = content.count(BOT_BOARD_WIRE_END)
    wanted = _bot_blurb(botname, sc)
    if begin_count or end_count:
        if begin_count != 1 or end_count != 1:
            return "ambiguous", content
        begin = content.index(BOT_BOARD_WIRE_BEGIN)
        end_start = content.index(BOT_BOARD_WIRE_END)
        if end_start < begin:
            return "ambiguous", content
        end = end_start + len(BOT_BOARD_WIRE_END)
        if BOT_WIRE_MARKER in (content[:begin] + content[end:]):
            return "ambiguous", content
        if content.startswith("\r\n", end):
            end += 2
        elif content.startswith("\n", end):
            end += 1
        block = content[begin:end]
        source = BOT_BLURB_TEMPLATE.read_text(encoding="utf-8")
        canonical = source[source.index(BOT_BOARD_WIRE_BEGIN) :].rstrip("\n") + "\n"
        if _normalized_bot_block(block, botname) != canonical:
            return "ambiguous", content
        rendered = wanted.replace("\n", _line_sep(content))
        if block == rendered:
            return "current", content
        return "upgrade", content[:begin] + rendered + content[end:]

    legacy_span = _legacy_bot_span(content, botname)
    if legacy_span is not None:
        begin, end = legacy_span
        rendered = wanted.replace("\n", _line_sep(content))
        return "upgrade", content[:begin] + rendered + content[end:]
    if BOT_WIRE_MARKER in content:
        return "ambiguous", content
    sep = _line_sep(content)
    if not content:
        return "append", wanted.replace("\n", sep)
    return "append", content.rstrip("\r\n") + sep + sep + wanted.replace("\n", sep)


def wire_bots(profiles_dir: Path, sc: Path, *, dry: bool) -> list:
    """Install/upgrade the managed board blurb in existing bot SOUL.md files.

    A bot profile = <profiles_dir>/<name>/ containing a SOUL.md (the persona
    file every fresh `-p <name>` invocation loads — the only carrier that
    reaches handoff runs, which inherit no env). Exact current blocks are
    idempotent; exact legacy blocks migrate; customized/ambiguous blocks are
    preserved as ACTION NEEDED. Changes are backed up (.bak-<ts>) and
    unreadable templates or unwritable personas fail open. Profiles WITHOUT a SOUL.md are
    reported but not touched — inventing a persona file is not this
    installer's call. Returns status lines for the summary.
    """
    out = []
    if not profiles_dir.is_dir():
        return [f"  (no profiles directory at {profiles_dir} — nothing to wire)"]
    profiles = sorted(p for p in profiles_dir.iterdir() if p.is_dir())
    if not profiles:
        return [f"  (no profiles in {profiles_dir} — nothing to wire)"]
    for prof in profiles:
        soul = prof / "SOUL.md"
        label = f"{prof.name}/SOUL.md"
        if not soul.exists():
            out.append(
                f"  {label:<34} absent (no persona file — paste the "
                "blurb manually if this profile is a bot)"
            )
            continue
        try:
            original = soul.read_bytes()
            content = original.decode("utf-8")
            state, replacement = _classify_bot_soul(content, prof.name, sc)
            if state == "current":
                out.append(f"  {label:<34} wired (managed bot-board-v2 already current)")
                continue
            if state == "ambiguous":
                out.append(
                    f"  {label:<34} ACTION NEEDED — customized or malformed "
                    "coordination enrollment preserved"
                )
                continue
            if dry:
                verb = "upgrade recognized enrollment" if state == "upgrade" else "append blurb"
                out.append(f"  {label:<34} would {verb}")
                continue
            bak = rewrite_carrier_if_unchanged(soul, original, replacement.encode("utf-8"))
            if bak is None:
                out.append(
                    f"  {label:<34} ACTION NEEDED — persona changed during upgrade; "
                    "preserved current bytes"
                )
                continue
            verb = "upgraded recognized enrollment" if state == "upgrade" else "appended"
            out.append(f"  {label:<34} wired ({verb}; previous copy -> {bak.name})")
        except UnicodeError as exc:
            out.append(
                f"  {label:<34} ACTION NEEDED — persona is not valid UTF-8 and was preserved: {exc}"
            )
        except OSError as exc:
            out.append(
                f"  {label:<34} NOT WIRED — {type(exc).__name__}: {exc} "
                "(paste manually: examples/bot-soul-coordination.example.md)"
            )
    return out


def wire_profile_memories(profiles_dir: Path, sc: Path, *, dry: bool) -> list:
    """Wire NON-BOT profiles with the managed board rule in each
    SOUL-less profile's OWN memory store (<profiles>/<name>/memories/MEMORY.md).

    A profile is a full agent instance with its own memory — the main store's
    rule never reaches its sessions. Profiles WITH a SOUL.md are bots: the
    blurb (wire_bots) is their carrier, and wiring memory too would just cost
    tokens every turn, so they are skipped here. Migration/idempotency,
    backup, ambiguity refusal, and fail-open behavior come from wire_memory;
    the store is created when absent (the profile dir
    existing IS the fresh-profile case). Returns status lines.
    """
    out = []
    if not profiles_dir.is_dir():
        return [f"  (no profiles directory at {profiles_dir} — nothing to wire)"]
    profiles = sorted(p for p in profiles_dir.iterdir() if p.is_dir())
    if not profiles:
        return [f"  (no profiles in {profiles_dir} — nothing to wire)"]
    for prof in profiles:
        label = f"{prof.name}/memories/MEMORY.md"
        if (prof / "SOUL.md").exists():
            out.append(f"  {label:<34} skipped (bot — the SOUL.md blurb is its carrier)")
            continue
        mem = prof / "memories" / "MEMORY.md"
        try:
            if not dry:
                mem.parent.mkdir(parents=True, exist_ok=True)
            status = wire_memory(mem, sc, dry=dry)
            if dry and status.startswith("no agent memory store"):
                status = f"would create {mem} with the rule"
        except (OSError, UnicodeError) as exc:
            status = (
                f"NOT WIRED — {type(exc).__name__}: {exc} "
                "(paste manually: examples/memory-entry.example.md)"
            )
        out.append(f"  {label:<34} {status}")
    return out


def default_home() -> Path:
    """~/.hermes, honoring HERMES_HOME if the user relocated it."""
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def rewrite_carrier_if_unchanged(path: Path, original: bytes, replacement: bytes):
    """Atomically replace one carrier only if its preflight bytes are unchanged."""
    if path.read_bytes() != original:
        return None
    backup = path.with_name(f"{path.name}.bak-{stamp()}")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{stamp()}-{counter}")
        counter += 1
    shutil.copy2(path, backup)
    if backup.read_bytes() != original:
        with contextlib.suppress(OSError):
            backup.unlink()
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        if path.read_bytes() != original:
            return None
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
    return backup


def make_executable(p: Path) -> None:
    """chmod +x, best-effort (no-op / harmless on Windows)."""
    try:
        mode = p.stat().st_mode
        p.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def copy_one(name: str, dest_dir: Path, *, executable: bool, dry: bool) -> str:
    """Install one payload file. Returns a one-word status for the summary."""
    src = SRC / name
    if not src.exists():
        return f"MISSING-SRC ({src})"
    dst = dest_dir / name
    if dst.exists():
        if filecmp.cmp(src, dst, shallow=False):
            return "unchanged"
        # Different content already there -> back it up before replacing, so a
        # local edit is never lost. This is the "upgrade, don't clobber" path.
        bak = dest_dir / f"{name}.bak-{stamp()}"
        action = f"UPGRADED (prev -> {bak.name})"
        if not dry:
            shutil.copy2(dst, bak)
            shutil.copy2(src, dst)
            if executable:
                make_executable(dst)
        return action
    if not dry:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if executable:
            make_executable(dst)
    return "installed"


def copy_tree(src: Path, dst: Path, *, dry: bool) -> list:
    """Recursively copy one directory's contents into another, idempotently.

    Mirrors copy_one's never-clobber policy: a differing destination file is
    backed up to <name>.bak-<timestamp> before replacement. Returns per-file
    status lines for the install summary.
    """
    out = []
    if not src.is_dir():
        return [f"MISSING-SRC ({src})"]
    for item in sorted(src.iterdir()):
        if item.is_dir():
            out.extend(copy_tree(item, dst / item.name, dry=dry))
            continue
        rel = item.name
        target = dst / rel
        if target.exists() and filecmp.cmp(item, target, shallow=False):
            out.append(f"  {rel:<28} unchanged")
            continue
        bak = target.with_name(f"{target.name}.bak-{stamp()}")
        action = f"UPGRADED (prev -> {bak.name})" if target.exists() else "installed"
        if not dry:
            dst.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.copy2(target, bak)
            shutil.copy2(item, target)
        out.append(f"  {rel:<28} {action}")
    return out


def copy_one_file(src: Path, dst: Path, *, dry: bool) -> str:
    """Idempotent single-file copy with backup-on-change (mirrors copy_one)."""
    if not src.exists():
        return f"MISSING-SRC ({src})"
    if dst.exists() and filecmp.cmp(src, dst, shallow=False):
        return "unchanged"
    bak = dst.with_name(f"{dst.name}.bak-{stamp()}")
    action = f"UPGRADED (prev -> {bak.name})" if dst.exists() else "installed"
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
    return action


def install_skill_bundle(skill_dest: Path, *, dry: bool) -> list:
    """Copy the skill bundle (SKILL.md + references/ + templates/ + examples/)
    into the agent's skills tree, mirroring a `hermes skills install` copy.
    Returns status lines; never raises on missing sources (fail-open)."""
    out = []
    for name, _ in SKILL_BUNDLE:
        src = SKILL_DIR / name
        if not src.exists():
            out.append(f"  {name:<28} MISSING-SRC ({src})")
            continue
        if src.is_dir():
            out.extend(copy_tree(src, skill_dest / name, dry=dry))
        else:
            out.append(f"  {name:<28} {copy_one_file(src, skill_dest / name, dry=dry)}")
    return out


def seed_manifest(state_dir: Path, *, do_seed: bool, dry: bool) -> str:
    """Optionally seed an EMPTY cron manifest from the example. Never overwrite
    an existing one (it declares what the user's real crons touch)."""
    dst = state_dir / "cron_resources.json"
    if dst.exists():
        return "preserved (already present — untouched)"
    if not do_seed:
        return (
            f"absent (optional; seed with:  cp {EXAMPLES / 'cron_resources.example.json'}  {dst})"
        )
    ex = EXAMPLES / "cron_resources.example.json"
    if not ex.exists():
        return f"MISSING-EXAMPLE ({ex})"
    if not dry:
        state_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ex, dst)
    return "seeded from example (edit it to match YOUR crons)"


def find_bash():
    """Return the path to a *working* bash, or None.

    On Windows, PATH usually surfaces the WSL launcher stub
    (C:\\Windows\\System32\\bash.exe); with no distro installed it prints a
    "use wsl.exe --install" notice and exits non-zero, so it cannot run our
    POSIX selftests (this is exactly what reddened Windows CI once). Git for
    Windows ships a real bash, so we add it as a candidate (derived from the
    git executable and the usual install roots) and PROBE every candidate --
    only a bash that actually echoes our marker is accepted. Returns the first
    that works, else None.
    """
    candidates = []
    for var in ("COORD_BASH", "BASH"):
        v = os.environ.get(var)
        if v:
            candidates.append(v)
    which = shutil.which("bash")
    if which:
        candidates.append(which)
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            root = Path(git).resolve().parent.parent  # ...\\Git\\cmd\\git.exe -> ...\\Git
            candidates.append(str(root / "bin" / "bash.exe"))
            candidates.append(str(root / "usr" / "bin" / "bash.exe"))
        for env_var in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_var)
            if base:
                candidates.append(str(Path(base) / "Git" / "bin" / "bash.exe"))
    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            probe = subprocess.run(  # nosec B603 B607 -- probing a discovered bash, fixed argv
                [cand, "-c", "echo __coord_bash_ok__"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and "__coord_bash_ok__" in (probe.stdout or ""):
            return cand
    return None


def run_suites(dest_dir: Path) -> tuple[int, bool]:
    """Run installed selftests and return ``(rc, complete)``.

    Each suite uses scratch state. ``complete`` is false when POSIX suites could
    not run because no working Bash was available.
    """
    bash = find_bash()
    complete = bash is not None
    failed = []
    if bash is None:
        print("  [skip] no working bash on this machine — POSIX selftests can't")
        print("         self-run here. The files ARE installed; prove those suites in")
        print("         Git Bash or WSL:")
        for suite in SUITES:
            print(f"           bash {dest_dir / suite}")
    sc = dest_dir / "session_coord.py"
    guard = dest_dir / "coord_guard.sh"
    env = dict(os.environ, SC=str(sc), GUARD=str(guard), COORD_SC=str(sc))
    # selftest_cron exercises copied/profile-local engine launch paths. Keep the
    # installed scripts directory importable so those copies resolve the new
    # mandatory session_coord_wakes sibling without mutating any profile.
    prior_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(dest_dir)
        if not prior_pythonpath
        else os.pathsep.join((str(dest_dir), prior_pythonpath))
    )
    # A caller's exported HERMES_COORD_ID would make every suite register and
    # claim under that same session (v2.3.2 honors it as the default --id),
    # turning the verify run into a self-collision on the live board. The
    # suites manage their own ids and scratch DBs — scrub it.
    env.pop("HERMES_COORD_ID", None)
    if bash is not None:
        for suite in SUITES:
            path = dest_dir / suite
            if not path.exists():
                print(f"  ! {suite}: not installed, cannot verify")
                failed.append(suite)
                continue
            proc = subprocess.run(  # nosec B603 B607 -- discovered bash + fixed argv, no shell
                [bash, str(path)], env=env, capture_output=True, text=True
            )
            last = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
            mark = "ok " if proc.returncode == 0 else "FAIL"
            print(f"  [{mark}] {suite}: {last}")
            if proc.returncode != 0:
                failed.append(suite)
                # surface a little context on failure
                for line in proc.stdout.strip().splitlines()[-6:]:
                    print(f"        {line}")
                if proc.stderr.strip():
                    print(f"        stderr: {proc.stderr.strip().splitlines()[-1]}")
    for suite in PYTHON_SUITES:
        path = dest_dir / suite
        if not path.exists():
            print(f"  ! {suite}: not installed, cannot verify")
            failed.append(suite)
            continue
        proc = subprocess.run(  # nosec B603 -- installed fixed Python suite, no shell
            [sys.executable, str(path)], env=env, capture_output=True, text=True
        )
        last = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
        mark = "ok " if proc.returncode == 0 else "FAIL"
        print(f"  [{mark}] {suite}: {last}")
        if proc.returncode != 0:
            failed.append(suite)
            for line in proc.stdout.strip().splitlines()[-6:]:
                print(f"        {line}")
            if proc.stderr.strip():
                print(f"        stderr: {proc.stderr.strip().splitlines()[-1]}")
    return (1 if failed else 0), complete


def choose_plugin_action(action=None, *, dry=False):
    print("\nOptional Hermes plugin")
    print("The coordination board works without the plugin: claims, conflict detection,")
    print(
        'priorities and manual coordination remain available. The plugin '
        'adds automatic continuation'
    )
    print("prompts into compatible, running Hermes sessions. It does not start a stopped app.")
    print("Installing it makes a parent wait for all delegated agents to report before continuing.")
    print("No process is restarted; removal preserves that shared delegation setting.")
    print("Rerun this installer to add/upgrade it later, or remove only the plugin.")
    if action is not None:
        return action
    if dry or not sys.stdin.isatty():
        print("No interactive choice: preserving the current plugin state (--plugin keep).")
        return "keep"
    print("  1. Do not install/change the plugin (keep the current setup; default)")
    print("  2. Install or upgrade the optional plugin")
    print("  3. Remove only the plugin; keep the board, claims, skills and other settings")
    print("  0. Cancel without changes")
    choices = {
        "": "keep",
        "1": "keep",
        "no": "keep",
        "n": "keep",
        "2": "install",
        "yes": "install",
        "y": "install",
        "3": "remove",
        "0": "cancel",
    }
    while True:
        try:
            answer = input("Choose [1]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "cancel"
        if answer in choices:
            return choices[answer]
        print("Please choose 1, 2, 3 or 0.")


def run_plugin_action(args, action):
    if action == "keep":
        return 0
    helper = HERE / "hermes_setup.py"
    command = [
        sys.executable,
        str(helper),
        "remove" if action == "remove" else ("check" if args.check else "setup"),
    ]
    for profile in args.profile or ["default"]:
        command.extend(["--profile", profile])
    command.extend(["--hermes", args.hermes])
    command.extend("--hermes-arg=" + token for token in args.hermes_arg)
    if action == "install":
        command.extend(["--plugin-path", str(args.plugin_path)])
    elif args.check:
        command.append("--check")
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        # Execute the selected local helper with argv; never parse a shell command.
        return subprocess.run(command, check=False).returncode  # nosec B603
    except (OSError, KeyboardInterrupt) as exc:
        print("Plugin operation did not complete: " + str(exc), file=sys.stderr)
        return 1


def main() -> int:
    home = default_home()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--dest",
        type=Path,
        default=Path(os.environ.get("HERMES_SCRIPTS_DIR", home / "scripts")),
        help="scripts directory to install into (default: ~/.hermes/scripts)",
    )
    ap.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("HERMES_STATE_DIR", home / "state")),
        help="state directory for the board DB + master switch (default: ~/.hermes/state)",
    )
    ap.add_argument(
        "--seed-manifest",
        action="store_true",
        help="also seed an example cron_resources.json if none exists",
    )
    ap.add_argument(
        "--no-verify", action="store_true", help="skip running the selftest suites after install"
    )
    ap.add_argument(
        "--no-wire-memory",
        action="store_true",
        help="skip writing the standing memory rule "
        "(scripts + board only; see "
        "examples/memory-entry.example.md)",
    )
    ap.add_argument(
        "--memory-file",
        type=Path,
        default=None,
        help="agent memory store to wire the rule into "
        "(default: <HERMES_HOME|~/.hermes>/memories/"
        "MEMORY.md)",
    )
    ap.add_argument(
        "--no-wire-bots",
        action="store_true",
        help="skip appending the coordination blurb to existing "
        "bot profiles' SOUL.md files (see "
        "examples/bot-soul-coordination.example.md)",
    )
    ap.add_argument(
        "--no-wire-profiles",
        action="store_true",
        help="skip wiring the standing memory rule into non-bot "
        "profiles' own memory stores "
        "(<profiles>/<name>/memories/MEMORY.md)",
    )
    ap.add_argument(
        "--profiles-dir",
        type=Path,
        default=None,
        help="profiles directory holding bot profiles "
        "(default: <HERMES_HOME|~/.hermes>/profiles, or "
        "$HERMES_COORD_PROFILES_DIR)",
    )
    ap.add_argument(
        "--skill-dest",
        type=Path,
        default=None,
        help="skills directory to install the skill bundle into "
        "(default: <HERMES_HOME|~/.hermes>/skills/"
        "multi-session-coordination)",
    )
    ap.add_argument(
        "--no-skill",
        action="store_true",
        help="skip installing the skill bundle (SKILL.md + references/ + templates/ + examples/)",
    )
    ap.add_argument(
        "--check", action="store_true", help="dry run: report what WOULD change, touch nothing"
    )
    ap.add_argument(
        "--plugin",
        choices=("keep", "install", "remove"),
        help="keep current plugin state, install/upgrade it, or remove only it; asks on a terminal",
    )
    ap.add_argument("--plugin-path", type=Path, help="reviewed session-coord-native Git checkout")
    ap.add_argument(
        "--profile",
        action="append",
        default=[],
        help="Hermes profile for plugin changes; repeatable; default: default",
    )
    ap.add_argument("--hermes", default="hermes", help="Hermes executable for optional integration")
    ap.add_argument("--hermes-arg", action="append", default=[], help="extra Hermes launcher token")
    args = ap.parse_args()
    for profile in args.profile:
        if profile != "default" and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile) is None:
            ap.error("invalid profile name: " + repr(profile))
    plugin_action = choose_plugin_action(args.plugin, dry=args.check)
    if plugin_action == "cancel":
        print("Cancelled; nothing changed.")
        return 0
    if plugin_action == "install" and args.plugin_path is None:
        if sys.stdin.isatty() and not args.check:
            try:
                value = input(
                    "Path to the reviewed session-coord-native Git checkout (blank cancels): "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                value = ""
            if not value:
                print("Cancelled; nothing changed.")
                return 0
            args.plugin_path = Path(value).expanduser()
        else:
            ap.error("--plugin install requires --plugin-path; use --plugin keep for standalone")
    if plugin_action == "install":
        args.plugin_path = args.plugin_path.expanduser().resolve()
        if not args.plugin_path.is_dir() or not (args.plugin_path / "plugin.yaml").is_file():
            ap.error("--plugin-path must contain the reviewed session-coord-native plugin.yaml")
    if plugin_action == "remove":
        # A plugin-only removal must never copy, upgrade or delete the board.
        return run_plugin_action(args, plugin_action)

    dest = args.dest.expanduser().resolve()
    state = args.state_dir.expanduser().resolve()
    dry = args.check

    print("session-coord installer")
    print(f"  source:      {HERE}")
    print(f"  scripts ->   {dest}")
    print(f"  state   ->   {state}")
    if dry:
        print("  MODE: --check (dry run, nothing will be written)")
    print()

    # 1. payload
    print("Files:")
    for name, executable in PAYLOAD.items():
        status = copy_one(name, dest, executable=executable, dry=dry)
        print(f"  {name:<22} {status}")

    # 2. state dir + protected data
    print("\nState:")
    if not dry:
        state.mkdir(parents=True, exist_ok=True)
    db = state / "session_coordination.db"
    db_state = "preserved (untouched)" if db.exists() else "absent (created lazily on first use)"
    print(f"  {'session_coordination.db':<26} {db_state}")
    manifest_state = seed_manifest(state, do_seed=args.seed_manifest, dry=dry)
    print(f"  {'cron_resources.json':<26} {manifest_state}")

    # 3. skill bundle (SKILL.md + references/ + templates/ + examples/)
    skill_dest = (
        args.skill_dest
        if args.skill_dest is not None
        else home / "skills" / "multi-session-coordination"
    )
    print(f"\nSkill bundle -> {skill_dest}" + ("   (skipped: --no-skill)" if args.no_skill else ""))
    if not args.no_skill:
        for line in install_skill_bundle(skill_dest.resolve(), dry=dry):
            print(line)

    # 4. master switch state (never changed by install)
    sentinel_env = os.environ.get("HERMES_COORD_DISABLED_FILE")
    sentinel = Path(sentinel_env) if sentinel_env else state / "coordination_disabled"
    switch = "DISABLED (sentinel present)" if sentinel.exists() else "ENABLED (default)"
    print(f"\nMaster switch: {switch}  (this installer never flips it)")
    if sentinel.exists():
        print(f"  re-enable with:  python3 {dest / 'session_coord.py'} enable")

    # 5. verify
    rc = 0
    verification_complete = False
    if dry:
        print("\n(dry run complete — re-run without --check to apply)")
    elif args.no_verify:
        print("\nSkipped verification (--no-verify). Run the suites yourself:")
        for s in SUITES:
            print(f"  bash {dest / s}")
        for s in PYTHON_SUITES:
            print(f"  {sys.executable} {dest / s}")
    else:
        print("\nVerifying (scratch DBs only — your board is untouched):")
        rc, verification_complete = run_suites(dest)
        if rc != 0:
            verdict = (
                "SOME SUITES FAILED — see above. The install copied files but "
                "could not prove itself on this machine."
            )
        elif verification_complete:
            verdict = "All suites green. session-coord is installed and verified."
        else:
            verdict = (
                "Available Python suites passed; POSIX suites were skipped. "
                "The install is usable but verification is incomplete on this machine."
            )
        print("\n" + verdict)

    # 6. wire the agent in (standing memory rule)
    memory_file = (
        args.memory_file if args.memory_file is not None else home / "memories" / "MEMORY.md"
    )
    sc = dest / "session_coord.py"
    profiles_dir = (
        args.profiles_dir
        if args.profiles_dir is not None
        else Path(os.environ.get("HERMES_COORD_PROFILES_DIR", home / "profiles")).expanduser()
    )
    wiring_action_needed = False
    if rc == 0 and not dry:
        print("\nAgent wiring (standing memory rule):")
        if args.no_wire_memory:
            print("  skipped (--no-wire-memory). Give your agent this entry:")
            print(board_memory_block(sc))
            print("  (canonical copy: examples/memory-entry.example.md)")
        else:
            status = wire_memory(memory_file, sc, dry=False)
            print(f"  {status}")
            wiring_action_needed = wiring_action_needed or status.startswith("ACTION NEEDED")
            if status.startswith("NOT WIRED"):
                print(board_memory_block(sc))

        # 7. wire existing bot profiles in (SOUL.md blurb — bots enroll via
        # persona, not memory: handoff runs inherit no env, and each bot
        # needs its own --surface bot:<name>).
        print("\nBot wiring (SOUL.md coordination blurb):")
        if args.no_wire_bots:
            print(
                "  skipped (--no-wire-bots). Blurb for manual paste: "
                "examples/bot-soul-coordination.example.md"
            )
        else:
            for line in wire_bots(profiles_dir, sc, dry=False):
                print(line)
                wiring_action_needed = wiring_action_needed or "ACTION NEEDED" in line

        # 8. wire non-bot profiles in (their OWN memory stores — a profile is
        # a full agent instance; the main store's rule never reaches it).
        print("\nProfile wiring (standing rule into each profile's own memory):")
        if args.no_wire_profiles:
            print(
                "  skipped (--no-wire-profiles). Entry for manual paste: "
                "examples/memory-entry.example.md"
            )
        else:
            for line in wire_profile_memories(profiles_dir, sc, dry=False):
                print(line)
                wiring_action_needed = wiring_action_needed or "ACTION NEEDED" in line
        print(
            "  (profiles/bots created LATER need wiring too — re-run this "
            "installer; `session_coord.py status` flags unenrolled ones)"
        )

        print("\nQuick start:")
        sc_arg = shlex.quote(str(sc))
        print(f"  ID=$(python3 {sc_arg} register --task 'my task' --surface cli)")
        print(f'  python3 {sc_arg} claim --id "$ID" --res file:/some/path')
        print(f'  python3 {sc_arg} done  --id "$ID"')
        print(f"  python3 {sc_arg} switch          # master on/off state")
    elif dry:
        print("\nAgent wiring (dry run):")
        if args.no_wire_memory:
            print("  skipped (--no-wire-memory)")
        else:
            status = wire_memory(memory_file, sc, dry=True)
            print(f"  {status}")
            wiring_action_needed = wiring_action_needed or status.startswith("ACTION NEEDED")
        print("\nBot wiring (dry run):")
        if args.no_wire_bots:
            print("  skipped (--no-wire-bots)")
        else:
            for line in wire_bots(profiles_dir, sc, dry=True):
                print(line)
                wiring_action_needed = wiring_action_needed or "ACTION NEEDED" in line
        print("\nProfile wiring (dry run):")
        if args.no_wire_profiles:
            print("  skipped (--no-wire-profiles)")
        else:
            for line in wire_profile_memories(profiles_dir, sc, dry=True):
                print(line)
                wiring_action_needed = wiring_action_needed or "ACTION NEEDED" in line
    if rc or wiring_action_needed:
        return rc or 1
    return run_plugin_action(args, plugin_action)


if __name__ == "__main__":
    sys.exit(main())
