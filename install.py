#!/usr/bin/env python3
# EDIT HISTORY (newest first)
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
SRC = HERE / "scripts"
EXAMPLES = HERE / "examples"

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

    # 3. master switch state (never changed by install)
    sentinel_env = os.environ.get("HERMES_COORD_DISABLED_FILE")
    sentinel = Path(sentinel_env) if sentinel_env else state / "coordination_disabled"
    switch = "DISABLED (sentinel present)" if sentinel.exists() else "ENABLED (default)"
    print(f"\nMaster switch: {switch}  (this installer never flips it)")
    if sentinel.exists():
        print(f"  re-enable with:  python3 {dest / 'session_coord.py'} enable")

    # 4. verify
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

    # 5. wire the agent in (standing memory rule)
    memory_file = (args.memory_file if args.memory_file is not None
                   else home / "memories" / "MEMORY.md")
    sc = dest / "session_coord.py"
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

        print("\nQuick start:")
        print(f"  ID=$(python3 {sc} register --task 'my task' --surface cli)")
        print(f"  python3 {sc} claim --id \"$ID\" --res file:/some/path")
        print(f"  python3 {sc} done  --id \"$ID\"")
        print(f"  python3 {sc} switch          # master on/off state")
    elif dry:
        print("\nAgent wiring (dry run):")
        print(f"  {wire_memory(memory_file, sc, dry=True)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
