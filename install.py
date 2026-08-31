#!/usr/bin/env python3
# EDIT HISTORY (newest first)
# 2026-08-31 | claude-fable-5 | anthropic | desktop session | BOT WIRING (v2.4.0): new step 7 appends the SOUL.md coordination blurb (marker "session-coord (bot-wire v1)", shipped inside templates/bot-soul-coordination.md) to every existing profiles/*/SOUL.md — default ON, --no-wire-bots opt-out, --profiles-dir/$HERMES_COORD_PROFILES_DIR override; idempotent marker check, .bak-<ts> backup, <botname> substituted, profiles without a SOUL.md reported-not-touched, fail-open like wire_memory. Step 8 wire_profile_memories(): SOUL-less (non-bot) profiles get the standing rule appended to their OWN memories/MEMORY.md (created if absent; --no-wire-profiles opt-out) — a profile is a full agent instance the main store's rule never reaches; bot profiles skipped (blurb is their carrier). Closes the enrollment gap: install used to wire the MAIN profile's memory only, so bots AND profiles missed the docs paragraph and stayed invisible to the board.  # noqa: E501
# 2026-08-30 | deepseek-v4-flash | custom | desktop session | OFFICIAL-SKILL PACKAGING: payload/examples moved under skills/multi-session-coordination/ (the Hermes skill layout — hub/tap/direct-URL installs copy only that subtree). New step 6: copies the skill bundle (SKILL.md + references/ + templates/ + examples/, NOT scripts/ — those already land in the scripts dir) to <home>/skills/multi-session-coordination by default (--skill-dest override, --no-skill opt-out), idempotent with backup-on-change like the payload. Docstring updated; everything else unchanged.  # noqa: E501
# 2026-08-26 | deepseek-v4-flash | custom | desktop session | WIRE-IN STEP: default ON appends the canonical standing memory entry ("session-coord (wire v1)" marker) to the agent memory store (default <HERMES_HOME|~/.hermes>/memories/MEMORY.md; --memory-file override; --no-wire-memory opt-out). Idempotent marker check, .bak-<ts> backup before append, store created only when the memories/ parent already exists, fail-open (absent/unwritable store prints the entry for manual placement, never fails the install); entry text format()s the real --dest script path. run_suites scrubs HERMES_COORD_ID from the verify env (v2.3.2 register/claim honor it as default --id — a caller's exported id made every suite self-collide on the live board). CI smoke test extended: re-run + exactly-one-marker assertion.  # noqa: E501
"""install.py — set up (or upgrade) session-coord on this machine.

Pure standard library, no network, cross-platform (Linux / macOS / Windows,
Python >= 3.8). Safe to re-run: it UPGRADES an existing install in place and
never touches your live board data.

What it does
------------
1. Copies the CLI + cron guard + selftests into a scripts directory
   (default: ~/.hermes/scripts, override with --dest or $HERMES_SCRIPTS_DIR).
2. Creates the state directory the board and master switch live in
   (default: ~/.hermes/state, override with --state-dir or $HERMES_STATE_DIR).
3. NEVER clobbers your data: the board DB (session_coordination.db) and an
   existing cron manifest (cron_resources.json) are left exactly as they are.
   Any script it replaces whose contents differ is backed up to
   <name>.bak-<timestamp> first, so a local modification is never lost silently.
4. Verifies the install by running the bundled selftest suites (skip with
   --no-verify) through a real bash. On a machine with no working bash it
   says so and skips verification rather than failing — the files are still
   installed; a POSIX shell is only needed to PROVE them.
5. Wires the agent in: appends the canonical standing memory rule
   ("always consult the coordination board", marker `session-coord (wire
   v1)`) to the agent memory store (default:
   <HERMES_HOME|~/.hermes>/memories/MEMORY.md; --memory-file overrides;
   --no-wire-memory skips). Idempotent (marker check → re-runs are no-ops),
   backed up (.bak-<ts>) before any append, and fail-open: an absent or
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
7. Wires existing BOT profiles in: appends the SOUL.md coordination blurb
   (templates/bot-soul-coordination.md, `<botname>` substituted, marker
   `session-coord (bot-wire v1)`) to every <profiles>/<name>/SOUL.md
   (--profiles-dir overrides; --no-wire-bots skips). Idempotent, backed up
   (.bak-<ts>), fail-open; profiles with no SOUL.md are reported, never
   invented (they are step 8's job instead).
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
import filecmp
import os
import shutil
import stat
import subprocess  # nosec B404 -- only used to run the bundled selftests; opt out via --no-verify
import sys
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
    "coord_guard.sh": False,
    "selftest.sh": True,
    "selftest_priority.sh": True,
    "selftest_cron.sh": True,
    "selftest_toggle.sh": True,
}
SUITES = ["selftest.sh", "selftest_priority.sh", "selftest_cron.sh", "selftest_toggle.sh"]

# Data files this installer must NEVER overwrite (they hold live state).
PROTECTED = ("session_coordination.db", "cron_resources.json")

# Idempotency marker for the standing memory entry. The phrase is part of
# the entry itself; a re-run (or an upgrade, or a re-paste) that finds it in
# the store skips instead of duplicating. Keep it byte-stable.
WIRE_MARKER = "session-coord (wire v1)"

# Idempotency + audit marker for the bot SOUL.md blurb (step 7). The marker
# line ships inside the template itself; `session_coord.py status` uses the
# same string to flag persona-bearing profiles that never enrolled. Keep it
# byte-stable and in lock-step with templates/bot-soul-coordination.md and
# BOT_WIRE_MARKER in session_coord.py.
BOT_WIRE_MARKER = "session-coord (bot-wire v1)"
BOT_BLURB_TEMPLATE = SKILL_DIR / "templates" / "bot-soul-coordination.md"

# The canonical standing memory rule. Kept as a single line — memory stores
# separate entries by blank/§ delimiters, and one line is what a wrapping
# paste cannot mangle. {sc} is substituted at write time with the absolute
# path of the installed CLI, so the rule always names the real script.
WIRE_ENTRY = (
    "STANDING RULE — session-coord (wire v1): ALWAYS deconflict via the "
    "coordination board before touching shared resources (files, skills, "
    "memory, cron store, remote boxes, desktop UI) — any concurrent "
    "session/bot/cron may be mid-task. First: `python3 {sc} status`. Then "
    "register once (`ID=$(python3 {sc} register --task '...')`); claim "
    "before writes (`python3 {sc} claim --id $ID --res <keys> --wait`); "
    "`python3 {sc} done --id $ID` at task end. Full protocol: skill "
    "multi-session-coordination. Off-switch: `python3 {sc} disable`."
)


def _memory_append_text(content: str, entry: str) -> str:
    """Append an entry to Hermes memory-store text, byte-faithful to the
    store's own writer (memory_tool.py: entries joined by \\n§\\n, trailing
    newlines stripped — the reader splits on \\n§\\n, so a glued entry would
    inherit the previous entry)."""
    content = content.rstrip("\n")
    if not content:
        return entry
    return content + "\n§\n" + entry


def wire_memory(memory_file: Path, sc: Path, *, dry: bool) -> str:
    """Append the standing coordination rule to the agent's memory store.

    Idempotent (marker byte-check), append-only, backed up first, and
    fail-open (Rule 2): an absent store with no memories/ parent — or an
    unwritable one — returns a "NOT WIRED" status naming the manual path and
    never raises, so the install cannot fail on the wiring step. A store
    whose parent directory exists (a real agent profile) IS created, since
    that is the fresh-profile case the wiring exists for. Returns a
    one-line status for the install summary.
    """
    entry = WIRE_ENTRY.format(sc=sc)
    try:
        if memory_file.exists():
            content = memory_file.read_text(encoding="utf-8")
            if WIRE_MARKER in content:
                return "wired (already present — idempotent skip)"
            if dry:
                return f"would append the rule to {memory_file}"
            bak = memory_file.with_name(f"{memory_file.name}.bak-{stamp()}")
            shutil.copy2(memory_file, bak)
            memory_file.write_text(_memory_append_text(content, entry),
                                   encoding="utf-8")
            return f"wired (appended; previous copy -> {bak.name})"
        if memory_file.parent.exists():
            if dry:
                return f"would create {memory_file} with the rule"
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            memory_file.write_text(entry, encoding="utf-8")
            return f"wired (created {memory_file})"
    except OSError as exc:
        return f"NOT WIRED — {type(exc).__name__}: {exc} (paste manually)"
    if dry:
        return f"no agent memory store found at {memory_file} (nothing to write)"
    return (
        f"NOT WIRED — no agent memory store at {memory_file}; paste the "
        "entry manually (examples/memory-entry.example.md)"
    )


def _bot_blurb(botname: str) -> str:
    """The SOUL.md enrollment block: everything in the template from the
    '## Shared-resource coordination' heading down (the prose above it is
    for humans reading the template, not for the bot's persona), with
    <botname> substituted. Raises OSError if the template is unreadable —
    callers treat that as fail-open."""
    text = BOT_BLURB_TEMPLATE.read_text(encoding="utf-8")
    idx = text.find("## Shared-resource coordination")
    block = text[idx:] if idx >= 0 else text
    return block.replace("<botname>", botname).rstrip("\n") + "\n"


def wire_bots(profiles_dir: Path, *, dry: bool) -> list:
    """Append the coordination blurb to every existing bot profile's SOUL.md.

    A bot profile = <profiles_dir>/<name>/ containing a SOUL.md (the persona
    file every fresh `-p <name>` invocation loads — the only carrier that
    reaches handoff runs, which inherit no env). Idempotent via
    BOT_WIRE_MARKER (shipped inside the blurb), backed up (.bak-<ts>) before
    any append, and fail-open: unreadable templates or unwritable personas
    report and never fail the install. Profiles WITHOUT a SOUL.md are
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
            out.append(f"  {label:<34} absent (no persona file — paste the "
                       "blurb manually if this profile is a bot)")
            continue
        try:
            content = soul.read_text(encoding="utf-8")
            if BOT_WIRE_MARKER in content:
                out.append(f"  {label:<34} wired (already present — idempotent skip)")
                continue
            blurb = _bot_blurb(prof.name)
            if dry:
                out.append(f"  {label:<34} would append the coordination blurb")
                continue
            bak = soul.with_name(f"SOUL.md.bak-{stamp()}")
            shutil.copy2(soul, bak)
            soul.write_text(content.rstrip("\n") + "\n\n" + blurb,
                            encoding="utf-8")
            out.append(f"  {label:<34} wired (appended; previous copy -> {bak.name})")
        except OSError as exc:
            out.append(f"  {label:<34} NOT WIRED — {type(exc).__name__}: {exc} "
                       "(paste manually: examples/bot-soul-coordination.example.md)")
    return out


def wire_profile_memories(profiles_dir: Path, sc: Path, *, dry: bool) -> list:
    """Wire NON-BOT profiles in: append the standing memory rule to each
    SOUL-less profile's OWN memory store (<profiles>/<name>/memories/MEMORY.md).

    A profile is a full agent instance with its own memory — the main store's
    rule never reaches its sessions. Profiles WITH a SOUL.md are bots: the
    blurb (wire_bots) is their carrier, and wiring memory too would just cost
    tokens every turn, so they are skipped here. Idempotent/backup/fail-open
    via wire_memory; the store is created when absent (the profile dir
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
            out.append(f"  {label:<34} skipped (bot — the SOUL.md blurb is "
                       "its carrier)")
            continue
        mem = prof / "memories" / "MEMORY.md"
        try:
            if not dry:
                mem.parent.mkdir(parents=True, exist_ok=True)
            status = wire_memory(mem, sc, dry=dry)
            if dry and status.startswith("no agent memory store"):
                status = f"would create {mem} with the rule"
        except OSError as exc:
            status = (f"NOT WIRED — {type(exc).__name__}: {exc} "
                      "(paste manually: examples/memory-entry.example.md)")
        out.append(f"  {label:<34} {status}")
    return out


def default_home() -> Path:
    """~/.hermes, honoring HERMES_HOME if the user relocated it."""
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


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
        action = (f"UPGRADED (prev -> {bak.name})" if target.exists()
                  else "installed")
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
            out.append(f"  {name:<28} "
                       f"{copy_one_file(src, skill_dest / name, dry=dry)}")
    return out


def seed_manifest(state_dir: Path, *, do_seed: bool, dry: bool) -> str:
    """Optionally seed an EMPTY cron manifest from the example. Never overwrite
    an existing one (it declares what the user's real crons touch)."""
    dst = state_dir / "cron_resources.json"
    if dst.exists():
        return "preserved (already present — untouched)"
    if not do_seed:
        return (f"absent (optional; seed with:  cp "
                f"{EXAMPLES / 'cron_resources.example.json'}  {dst})")
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
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and "__coord_bash_ok__" in (probe.stdout or ""):
            return cand
    return None


def run_suites(dest_dir: Path) -> int:
    """Run every selftest suite against the freshly installed copy. Each suite
    is self-contained (scratch DBs in a temp dir), so this is side-effect free
    for the real board. Returns process-style rc (0 = all green, or a graceful
    skip when the machine has no bash to run POSIX scripts)."""
    bash = find_bash()
    if bash is None:
        print("  [skip] no working bash on this machine — POSIX selftests can't")
        print("         self-run here. The files ARE installed; prove them in")
        print("         Git Bash or WSL:")
        for suite in SUITES:
            print(f"           bash {dest_dir / suite}")
        return 0
    sc = dest_dir / "session_coord.py"
    guard = dest_dir / "coord_guard.sh"
    env = dict(os.environ, SC=str(sc), GUARD=str(guard), COORD_SC=str(sc))
    # A caller's exported HERMES_COORD_ID would make every suite register and
    # claim under that same session (v2.3.2 honors it as the default --id),
    # turning the verify run into a self-collision on the live board. The
    # suites manage their own ids and scratch DBs — scrub it.
    env.pop("HERMES_COORD_ID", None)
    failed = []
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
    return 1 if failed else 0


def main() -> int:
    home = default_home()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dest", type=Path,
                    default=Path(os.environ.get("HERMES_SCRIPTS_DIR", home / "scripts")),
                    help="scripts directory to install into (default: ~/.hermes/scripts)")
    ap.add_argument("--state-dir", type=Path,
                    default=Path(os.environ.get("HERMES_STATE_DIR", home / "state")),
                    help="state directory for the board DB + master switch "
                         "(default: ~/.hermes/state)")
    ap.add_argument("--seed-manifest", action="store_true",
                    help="also seed an example cron_resources.json if none exists")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip running the selftest suites after install")
    ap.add_argument("--no-wire-memory", action="store_true",
                    help="skip writing the standing memory rule "
                         "(scripts + board only; see "
                         "examples/memory-entry.example.md)")
    ap.add_argument("--memory-file", type=Path, default=None,
                    help="agent memory store to wire the rule into "
                         "(default: <HERMES_HOME|~/.hermes>/memories/"
                         "MEMORY.md)")
    ap.add_argument("--no-wire-bots", action="store_true",
                    help="skip appending the coordination blurb to existing "
                         "bot profiles' SOUL.md files (see "
                         "examples/bot-soul-coordination.example.md)")
    ap.add_argument("--no-wire-profiles", action="store_true",
                    help="skip wiring the standing memory rule into non-bot "
                         "profiles' own memory stores "
                         "(<profiles>/<name>/memories/MEMORY.md)")
    ap.add_argument("--profiles-dir", type=Path, default=None,
                    help="profiles directory holding bot profiles "
                         "(default: <HERMES_HOME|~/.hermes>/profiles, or "
                         "$HERMES_COORD_PROFILES_DIR)")
    ap.add_argument("--skill-dest", type=Path, default=None,
                    help="skills directory to install the skill bundle into "
                         "(default: <HERMES_HOME|~/.hermes>/skills/"
                         "multi-session-coordination)")
    ap.add_argument("--no-skill", action="store_true",
                    help="skip installing the skill bundle (SKILL.md + "
                         "references/ + templates/ + examples/)")
    ap.add_argument("--check", action="store_true",
                    help="dry run: report what WOULD change, touch nothing")
    args = ap.parse_args()

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
    db_state = ("preserved (untouched)" if db.exists()
                else "absent (created lazily on first use)")
    print(f"  {'session_coordination.db':<26} {db_state}")
    manifest_state = seed_manifest(state, do_seed=args.seed_manifest, dry=dry)
    print(f"  {'cron_resources.json':<26} {manifest_state}")

    # 3. skill bundle (SKILL.md + references/ + templates/ + examples/)
    skill_dest = (args.skill_dest if args.skill_dest is not None
                  else home / "skills" / "multi-session-coordination")
    print(f"\nSkill bundle -> {skill_dest}"
          + ("   (skipped: --no-skill)" if args.no_skill else ""))
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
    if dry:
        print("\n(dry run complete — re-run without --check to apply)")
    elif args.no_verify:
        print("\nSkipped verification (--no-verify). Run the suites yourself:")
        for s in SUITES:
            print(f"  bash {dest / s}")
    else:
        print("\nVerifying (scratch DBs only — your board is untouched):")
        rc = run_suites(dest)
        print("\n" + ("All suites green. session-coord is installed and proven."
                      if rc == 0 else
                      "SOME SUITES FAILED — see above. The install copied files but "
                      "could not prove itself on this machine."))

    # 6. wire the agent in (standing memory rule)
    memory_file = (args.memory_file if args.memory_file is not None
                   else home / "memories" / "MEMORY.md")
    sc = dest / "session_coord.py"
    profiles_dir = (args.profiles_dir if args.profiles_dir is not None
                    else Path(os.environ.get("HERMES_COORD_PROFILES_DIR",
                                             home / "profiles")).expanduser())
    if rc == 0 and not dry:
        print("\nAgent wiring (standing memory rule):")
        if args.no_wire_memory:
            print("  skipped (--no-wire-memory). Give your agent this entry:")
            print(f"  {WIRE_ENTRY.format(sc=sc)}")
            print("  (canonical copy: examples/memory-entry.example.md)")
        else:
            status = wire_memory(memory_file, sc, dry=False)
            print(f"  {status}")
            if status.startswith("NOT WIRED"):
                print(f"  {WIRE_ENTRY.format(sc=sc)}")

        # 7. wire existing bot profiles in (SOUL.md blurb — bots enroll via
        # persona, not memory: handoff runs inherit no env, and each bot
        # needs its own --surface bot:<name>).
        print("\nBot wiring (SOUL.md coordination blurb):")
        if args.no_wire_bots:
            print("  skipped (--no-wire-bots). Blurb for manual paste: "
                  "examples/bot-soul-coordination.example.md")
        else:
            for line in wire_bots(profiles_dir, dry=False):
                print(line)

        # 8. wire non-bot profiles in (their OWN memory stores — a profile is
        # a full agent instance; the main store's rule never reaches it).
        print("\nProfile wiring (standing rule into each profile's own memory):")
        if args.no_wire_profiles:
            print("  skipped (--no-wire-profiles). Entry for manual paste: "
                  "examples/memory-entry.example.md")
        else:
            for line in wire_profile_memories(profiles_dir, sc, dry=False):
                print(line)
        print("  (profiles/bots created LATER need wiring too — re-run this "
              "installer; `session_coord.py status` flags unenrolled ones)")

        print("\nQuick start:")
        print(f"  ID=$(python3 {sc} register --task 'my task' --surface cli)")
        print(f"  python3 {sc} claim --id \"$ID\" --res file:/some/path")
        print(f"  python3 {sc} done  --id \"$ID\"")
        print(f"  python3 {sc} switch          # master on/off state")
    elif dry:
        print("\nAgent wiring (dry run):")
        print(f"  {wire_memory(memory_file, sc, dry=True)}")
        print("\nBot wiring (dry run):")
        for line in wire_bots(profiles_dir, dry=True):
            print(line)
        print("\nProfile wiring (dry run):")
        for line in wire_profile_memories(profiles_dir, sc, dry=True):
            print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
