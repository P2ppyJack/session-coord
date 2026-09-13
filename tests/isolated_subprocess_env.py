from __future__ import annotations

import os
from collections.abc import Mapping
from os import PathLike


_WINDOWS_RUNTIME_KEYS = ("SYSTEMROOT", "WINDIR", "COMSPEC")


def isolated_subprocess_env(
    home: str | PathLike[str],
    *,
    hermes_home: str | PathLike[str] | None = None,
    extra: Mapping[str, str | PathLike[str]] | None = None,
) -> dict[str, str]:
    """Build an isolated test env with required Windows runtime state."""
    isolated_home = os.fspath(home)
    env = {
        "HOME": isolated_home,
        "USERPROFILE": isolated_home,
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
    }
    if hermes_home is not None:
        env["HERMES_HOME"] = os.fspath(hermes_home)
    for key in _WINDOWS_RUNTIME_KEYS:
        if key in os.environ:
            env[key] = os.environ[key]
    if extra:
        env.update({key: os.fspath(value) for key, value in extra.items()})
    return env
