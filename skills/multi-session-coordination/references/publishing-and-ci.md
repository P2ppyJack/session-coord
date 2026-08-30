# Publishing & CI — the P2ppyJack/session-coord repo

The coordination engine is also shipped as a standalone public repo. When you
change the LIVE tool (`~/.hermes/scripts/session_coord.py` + `coord_guard.sh`)
and Toby wants the improvement shared, port it here too and keep the two in
lock-step.

## Where everything lives (so you don't have to search for it)

| Thing | Location |
|---|---|
| Local clone | `~/hermes-skill-dist/multi-session-coordination` |
| Remote | `git@github.com:P2ppyJack/session-coord.git` (SSH; branch `main`) |
| Publish account | P2ppyJack — **displays as "Feldsparian"** in Toby's logged-in Chrome (display name ≠ username). Real-name "Tobias Musser" attribution is deliberate (his call). |
| License | MIT |
| Live engine (source of truth for logic) | `~/.hermes/scripts/session_coord.py`, `~/.hermes/scripts/coord_guard.sh` |
| Live skill (this file's home) | `~/.hermes/skills/software-development/multi-session-coordination/` |

Auth: SSH for push (`ssh -T git@github.com` → "Hi P2ppyJack!"). Public-repo
REST reads are anonymous — **omit** the Authorization header (empty Bearer →
401; no header → 200). Writes need the SSH key; no PAT since Aug 2026.

## Repo layout differs from the live tool — port, don't copy blind

The engine lives under `skills/multi-session-coordination/scripts/` (the
official Hermes skill layout — the whole `skills/multi-session-coordination/`
subtree is the installable skill bundle: SKILL.md + references/ + templates/ +
scripts/ + examples/). The repo copies are CI-adapted: portable placeholder names
(`box:gpu-box-1` not Toby's real IPs), `$SC`/`$GUARD`/`$COORD_SC` env
resolution instead of live absolute paths, `contextlib.suppress` and em-dashes
for strict-ruff, `# noqa: E501` where the live copy has none. So porting a fix
is a semantic re-apply, then a comment-ignoring diff to confirm logic parity:
`diff <(grep -vE '^\s*#' live.py) <(grep -vE '^\s*#' repo.py)` — the only
differences should be that known cosmetic set.

`install.py` is a **repo-only artifact** (there is no copy in the live skill or
`~/.hermes/scripts`); the live engine is installed directly.

## CI matrix (`.github/workflows/tests.yml`)

- **lint** (ubuntu): `ruff --select E,F,W,I,UP,SIM,ISC,RUF,B --line-length 100`,
  `bandit` (annotate audited sites with `# nosec`, NOT ruff `# noqa` — the ruff
  profile excludes the `S` rules), `shellcheck` on `coord_guard.sh` +
  `wrapper.example.sh` + `selftest_toggle.sh` ONLY (not the other suites).
- **selftests** on a 3-OS × 3-Python matrix: {macos, ubuntu, windows} ×
  {3.8, 3.11, 3.13} = 9 jobs. Each runs all four suites + the install.py smoke
  test. Windows uses **Git Bash**, not WSL.

## HARD LESSON: local green ≠ CI green — always poll the Actions run

Passing every suite + linter locally does **not** mean CI passes. This bit us:
v2.3.0 and v2.3.1 were pushed after a full local green sweep and both went
**red on GitHub** — the Windows leg failed. Never tell Toby "CI will pass";
push, then VERIFY the run and only then report done.

Check the run without a token (anonymous REST):
```bash
curl -fsSL "https://api.github.com/repos/P2ppyJack/session-coord/actions/runs?per_page=3" -o /tmp/ci.json
# then parse in python (execute_code) — NOT an inline f-string with backslashes,
# which raises "f-string expression part cannot include a backslash"
```
Read job/step detail from `.../actions/runs/<id>/jobs`. Windows runners are
slow (~2–4 min); poll with a generous budget. If the job-log endpoint 302s to a
signed URL that anon can't fetch, read it in Toby's logged-in Chrome via
`browser_exec` instead. A step that *ran* the suites takes minutes; a fast ~1s
"success/failure" means it short-circuited (skip or early error) — check which.

## HARD LESSON: bare `bash` on Windows is the WSL stub, not a shell

`subprocess.run(["bash", ...])` on Windows resolves to
`C:\Windows\System32\bash.exe` — the **WSL launcher stub**. With no distro
installed (GitHub `windows-latest`) it prints "use `wsl.exe --install`" and
exits non-zero. That reddened the installer smoke test on all three Windows
Pythons while every OS still passed the 122 suite checks themselves.

Fix pattern (see `install.py::find_bash`): build a candidate list
(`COORD_BASH`/`BASH` env → `shutil.which("bash")` → Git-for-Windows bash
derived from `shutil.which("git")` parent and `%PROGRAMFILES%\Git\bin\bash.exe`)
and **probe each** with `bash -c 'echo <marker>'`, accepting only one that
exits 0 AND echoes the marker (rejects the stub). With no working bash, print
how to verify manually and exit 0 — files install fine; a POSIX shell is only
needed to PROVE them, so absence is a graceful skip, not a failure. Git Bash on
`windows-latest` means CI actually runs the suites (real proof, not a skip).

## Repo norms

Conventional Commits (`fix(scope): …`, `feat(scope): …`); scopes seen here:
`install`. One logical change per commit. Bump `CHANGELOG.md` (Keep a Changelog)
+ the version paragraph counts + `SKILL.md` verification counts together. Commit
LOCALLY and **ask Toby before `git push`** (his standing comms rule covers repo
writes too — though he pre-approved the push for this line of work).
