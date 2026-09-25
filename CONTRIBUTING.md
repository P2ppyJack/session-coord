# Contributing

Contributions are welcome for focused fixes, portability improvements, tests, and documentation.

## Development setup

Requirements:

- Python 3.8 or newer
- Bash for the shell suites
- Git

The runtime is standard-library-only. Install test and lint tools into an isolated environment; do not add generated environments or runtime databases to the repository.

## Before submitting

Run the same local checks used by CI:

```bash
bash skills/multi-session-coordination/scripts/selftest.sh
bash skills/multi-session-coordination/scripts/selftest_priority.sh
bash skills/multi-session-coordination/scripts/selftest_cron.sh
bash skills/multi-session-coordination/scripts/selftest_toggle.sh
python3 skills/multi-session-coordination/scripts/selftest_wakes.py
python3 -m pytest -q tests
ruff check --select E,F,W,I,UP,SIM,ISC,RUF,B --target-version py38 --line-length 100 \
  skills/multi-session-coordination/scripts/session_coord.py \
  skills/multi-session-coordination/scripts/session_coord_wakes.py \
  skills/multi-session-coordination/scripts/coord_resume_watchdog.py \
  install.py hermes_setup.py hermes_remove.py
```

Also run ShellCheck and Bandit when available; `.github/workflows/tests.yml` is the exact CI reference.

## Pull requests

- Use a focused branch and a Conventional Commit title.
- Explain the behavior change, compatibility impact, and exact test results.
- Add regression coverage for fixes.
- Preserve Python 3.8 compatibility and the dependency-free runtime.
- Never include credentials, personal paths, private infrastructure identifiers, production databases, or conversation/session records.
- Keep the standalone board independent from optional Hermes-native integration.

By contributing, you agree that your contribution is licensed under the MIT License in this repository.
