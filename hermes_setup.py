#!/usr/bin/env python3
"""Check or explicitly configure optional native Hermes continuation.

This helper is separate from ``install.py``: the default board install has no
Hermes dependency and never changes Hermes configuration or scheduling. This
file only uses public Hermes CLI operations and writes managed enrollment text
after every selected profile passes capability and Boolean-policy readback.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess  # nosec B404 -- fixed argv only; never shell=True
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

import install as board_installer

PLUGIN_NAME = "session-coord-native"
WAIT_KEY = "delegation.wait_for_all"
NATIVE_BEGIN = "<!-- BEGIN session-coord managed hermes-native-v1 -->"
NATIVE_END = "<!-- END session-coord managed hermes-native-v1 -->"
NATIVE_MARKER = "session-coord (hermes-native-wire v1)"
NATIVE_TEMPLATE = (
    NATIVE_BEGIN + "\n"
    "NATIVE CONTINUATION — session-coord (hermes-native-wire v1): This profile "
    "has passed the Hermes native-turn-source and joined-delegation checks. Before "
    "a claim that may yield, create an existing absolute checkpoint and run "
    "`python3 {sc} claim --id $ID --res <key> [--res <key2>] --yield "
    "--checkpoint /absolute/file`. Exit 75 means STOP immediately: do not poll, "
    "mutate, or continue the turn. Resume only from the targeted native turn, then "
    "run exactly `python3 {sc} continue --id BOARD --event EVENT` before the "
    "first resumed mutation. Stop/cancel must use `cancel-wait`. On-disk setup "
    "takes effect only in fresh Hermes processes; never infer activation in an "
    "already-running process.\n" + NATIVE_END
)
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class JsonError(ValueError):
    pass


class CommandResult:
    def __init__(self, argv: Sequence[str], returncode: int, stdout: str, stderr: str):
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Runner:
    """Fixed-argv subprocess runner. It never invokes a shell."""

    def __call__(self, argv: Sequence[str], timeout: int = 60) -> CommandResult:
        if isinstance(argv, (str, bytes)):
            raise TypeError("runner argv must be a sequence of tokens, not a command string")
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(  # nosec B603 -- caller provides fixed token argv
                list(argv),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
                env=env,
            )
            return CommandResult(argv, proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout.decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            return CommandResult(
                argv, 124, stdout, stderr + f"\ncommand timed out after {timeout}s"
            )
        except OSError as exc:
            return CommandResult(argv, 127, "", f"{type(exc).__name__}: {exc}")


def _hermes(
    runner: Runner, executable: Sequence[str], profile: str, *command: str
) -> CommandResult:
    return runner([*executable, "-p", profile, *command])


def _json_object(text: str, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise JsonError(f"{label} contains duplicate JSON key {key!r}")
            out[key] = value
        return out

    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except (json.JSONDecodeError, JsonError) as exc:
        raise JsonError(f"{label} returned malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise JsonError(f"{label} must return one JSON object")
    return value


def _json_value(text: str, label: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonError(f"{label} returned malformed JSON: {exc}") from exc


def _reason(result: CommandResult) -> str:
    detail = (result.stderr or result.stdout).strip()
    return detail.splitlines()[-1] if detail else f"command exited {result.returncode}"


def _git(runner: Runner, plugin: Path, *args: str) -> CommandResult:
    return runner(["git", "-C", str(plugin), *args], timeout=30)


def _source_revision(runner: Runner, plugin: Path) -> tuple[str | None, str | None]:
    rev = _git(runner, plugin, "rev-parse", "HEAD")
    if rev.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", rev.stdout.strip()):
        return None, "plugin path must be a Git worktree with a committed HEAD"
    return rev.stdout.strip().lower(), None


def _source_plugin_name(plugin: Path) -> tuple[str | None, str | None]:
    manifest = plugin / "plugin.yaml"
    try:
        content = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, f"cannot read plugin manifest {manifest}: {exc}"
    names = re.findall(r"^name:\s*['\"]?([^\s#'\"]+)", content, flags=re.MULTILINE)
    if len(names) != 1:
        return None, "plugin.yaml must declare exactly one top-level name"
    if names[0] != PLUGIN_NAME:
        return None, f"plugin.yaml declares {names[0]!r}; expected {PLUGIN_NAME!r}"
    return names[0], None


def _source_clean(runner: Runner, plugin: Path) -> tuple[bool | None, str | None]:
    dirty = _git(runner, plugin, "status", "--porcelain", "--untracked-files=all")
    if dirty.returncode != 0:
        return None, "could not inspect the plugin worktree"
    return not bool(dirty.stdout.strip()), None


def _installed_revision(runner: Runner, installed: Path) -> tuple[str | None, str | None]:
    if not installed.exists():
        return None, None
    rev = _git(runner, installed, "rev-parse", "HEAD")
    if rev.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", rev.stdout.strip()):
        return (
            None,
            "installed plugin is not a verifiable Git checkout; preserve it and resolve manually",
        )
    dirty = _git(runner, installed, "status", "--porcelain", "--untracked-files=all")
    if dirty.returncode != 0 or dirty.stdout.strip():
        return None, "installed plugin has local changes; preserve it and resolve manually"
    return rev.stdout.strip().lower(), None


def _path_uri(path: Path) -> str:
    """Return one percent-encoded argv token with no shell interpretation."""
    return path.resolve().as_uri()


def _native_block(coord: Path, newline: str = "\n") -> str:
    return NATIVE_TEMPLATE.format(sc=shlex.quote(str(coord))).replace("\n", newline)


def _native_state(content: str, coord: Path, kind: str = "memory") -> tuple[str, str]:
    begins = content.count(NATIVE_BEGIN)
    ends = content.count(NATIVE_END)
    if begins or ends:
        if begins != 1 or ends != 1:
            return "ambiguous", content
        begin = content.index(NATIVE_BEGIN)
        end_start = content.index(NATIVE_END)
        if end_start < begin:
            return "ambiguous", content
        end = end_start + len(NATIVE_END)
        if NATIVE_MARKER in (content[:begin] + content[end:]):
            return "ambiguous", content
        block = content[begin:end].replace("\r\n", "\n")
        if board_installer._template_pattern(NATIVE_TEMPLATE).fullmatch(block) is None:
            return "ambiguous", content
        wanted = _native_block(coord, board_installer._line_sep(content))
        original = content[content.index(NATIVE_BEGIN) : end]
        if original == wanted:
            return "current", content
        return "upgrade", content[: content.index(NATIVE_BEGIN)] + wanted + content[end:]
    if NATIVE_MARKER in content:
        return "ambiguous", content
    newline = board_installer._line_sep(content)
    block = _native_block(coord, newline)
    if not content:
        return "absent", block
    if kind == "bot":
        return "absent", content.rstrip("\r\n") + newline + newline + block
    return "absent", board_installer._memory_append_text(content, block)


def _carrier(profile: str, home: Path) -> tuple[Path, str]:
    soul = home / "SOUL.md"
    if profile != "default" and soul.is_file():
        return soul, "bot"
    return home / "memories" / "MEMORY.md", "memory"


def _board_state(path: Path, kind: str, profile: str, coord: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        content = path.read_bytes().decode("utf-8")
        if kind == "bot":
            return board_installer._classify_bot_soul(content, profile, coord)[0]
        return board_installer._classify_board_memory(content, coord)[0]
    except (OSError, UnicodeError):
        return "unreadable"


def _classify_native_file(path: Path, coord: Path, kind: str) -> tuple[str, str | None]:
    if not path.exists():
        return "absent", None
    try:
        content = path.read_bytes().decode("utf-8")
        return _native_state(content, coord, kind)[0], None
    except (OSError, UnicodeError) as exc:
        return "ambiguous", f"cannot read enrollment carrier: {type(exc).__name__}: {exc}"


def _atomic_enroll(
    path: Path,
    profile: str,
    coord: Path,
    kind: str,
    expected_digest: str,
) -> tuple[str, str]:
    """Compare-and-swap one managed native block with an adjacent backup."""
    try:
        if not path.is_file():
            return "failed", "enrollment carrier disappeared after preflight"
        original = path.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected_digest:
            return "failed", "enrollment carrier changed after preflight; preserved current bytes"
        content = original.decode("utf-8")
        if kind == "bot":
            board_state = board_installer._classify_bot_soul(content, profile, coord)[0]
        else:
            board_state = board_installer._classify_board_memory(content, coord)[0]
        if board_state != "current":
            return "failed", "board enrollment changed after preflight; native block not written"
        state, replacement = _native_state(content, coord, kind)
        if state == "current":
            return "current", "managed native block already current"
        if state == "ambiguous":
            return "ambiguous", "customized or malformed native enrollment was preserved"

        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{counter}")
            counter += 1
        shutil.copy2(path, backup)
        if backup.read_bytes() != original:
            with contextlib.suppress(OSError):
                backup.unlink()
            return "failed", "enrollment carrier changed while backing up; no replacement written"

        mode = stat.S_IMODE(path.stat().st_mode)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(replacement.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, mode)
            if path.read_bytes() != original:
                return "failed", "enrollment carrier changed before commit; replacement abandoned"
            os.replace(tmp_name, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
        return "current", f"managed native block written; previous copy -> {backup.name}"
    except (OSError, UnicodeError) as exc:
        return "failed", f"{type(exc).__name__}: {exc}"


def _validate_native(payload: dict[str, Any], *, require_wait_true: bool) -> str | None:
    if payload.get("supported") is not True:
        return str(payload.get("reason") or "native turn-source registration is unsupported")
    surfaces = payload.get("surfaces")
    if (
        not isinstance(surfaces, list)
        or not surfaces
        or not all(type(x) is str and x for x in surfaces)
    ):
        return "native-check surfaces must be a non-empty string list"
    if payload.get("wait_for_all_supported") is not True:
        return str(payload.get("reason") or "joined delegation policy helper is unavailable")
    wait = payload.get("wait_for_all")
    if type(wait) is not bool:
        return "native-check wait_for_all must be a true Boolean"
    if require_wait_true and wait is not True:
        return "native-check reports wait_for_all false after configuration"
    if payload.get("activation") != "fresh_process_only":
        return "native-check activation must be fresh_process_only"
    if type(payload.get("reason")) is not str:
        return "native-check reason must be a string"
    return None


def _plugins_enabled(result: CommandResult) -> tuple[bool | None, str | None]:
    if result.returncode != 0:
        if "Config key not set: plugins" in f"{result.stdout}\n{result.stderr}":
            return False, None
        return None, _reason(result)
    try:
        payload = _json_object(result.stdout, "config get plugins")
    except JsonError as exc:
        return None, str(exc)
    enabled = payload.get("enabled", [])
    disabled = payload.get("disabled", [])
    if not isinstance(enabled, list) or not isinstance(disabled, list):
        return None, "plugins.enabled and plugins.disabled must be lists"
    return PLUGIN_NAME in enabled and PLUGIN_NAME not in disabled, None


def _read_wait(result: CommandResult) -> tuple[bool | None, str | None]:
    if result.returncode != 0:
        return None, _reason(result)
    try:
        value = _json_value(result.stdout, f"config get {WAIT_KEY}")
    except JsonError as exc:
        return None, str(exc)
    if type(value) is not bool:
        return None, f"{WAIT_KEY} readback must be a true Boolean, not {type(value).__name__}"
    return value, None


def _read_native(
    result: CommandResult, *, require_wait_true: bool
) -> tuple[dict[str, Any] | None, str | None]:
    if result.returncode != 0:
        return None, _reason(result)
    try:
        payload = _json_object(result.stdout, "native-check")
    except JsonError as exc:
        return None, str(exc)
    error = _validate_native(payload, require_wait_true=require_wait_true)
    return (None, error) if error else (payload, None)


def _resolve_home(
    runner: Runner, hermes: Sequence[str], profile: str
) -> tuple[Path | None, str | None]:
    result = _hermes(runner, hermes, profile, "config", "path")
    if result.returncode != 0:
        return None, _reason(result)
    text = result.stdout.strip()
    path = Path(text).expanduser()
    if not text or not path.is_absolute() or path.name != "config.yaml":
        return None, "config path must return one absolute config.yaml path"
    return path.resolve().parent, None


def _profile_result(profile: str) -> dict[str, Any]:
    return {
        "profile": profile,
        "status": "unchecked",
        "reason": "",
        "plugin": "unknown",
        "wait_for_all": None,
        "native_supported": False,
        "surfaces": [],
        "native_enrollment": "unknown",
        "activation": None,
        "restart_required": False,
    }


def _preflight(
    runner: Runner,
    hermes: Sequence[str],
    profiles: Sequence[str],
    plugin: Path,
    coord: Path,
    source_sha: str,
) -> tuple[list[dict[str, Any]], bool]:
    results: list[dict[str, Any]] = []
    all_ok = True
    for profile in profiles:
        item = _profile_result(profile)
        home, error = _resolve_home(runner, hermes, profile)
        if error or home is None:
            item.update(status="profile_unavailable", reason=error or "profile unavailable")
            all_ok = False
            results.append(item)
            continue
        item["profile_home"] = str(home)
        doctor = _hermes(runner, hermes, profile, "plugins", "doctor", str(plugin), "--ci")
        if doctor.returncode != 0:
            item.update(
                status="unsupported_host",
                reason=(
                    "plugin registration Doctor failed; update Hermes to a host with the "
                    f"native-turn-source registrar: {_reason(doctor)}"
                ),
            )
            all_ok = False
            results.append(item)
            continue
        carrier, kind = _carrier(profile, home)
        item["carrier"] = str(carrier)
        item["carrier_kind"] = kind
        try:
            item["carrier_digest"] = hashlib.sha256(carrier.read_bytes()).hexdigest()
        except OSError as exc:
            item.update(status="action_needed", reason=f"cannot snapshot enrollment carrier: {exc}")
            all_ok = False
            results.append(item)
            continue
        board_state = _board_state(carrier, kind, profile, coord)
        item["board_enrollment"] = board_state
        native_state, native_error = _classify_native_file(carrier, coord, kind)
        item["native_enrollment"] = native_state
        if board_state != "current":
            item.update(
                status="board_setup_required",
                reason=(
                    "board managed enrollment is not current; run install.py before native setup"
                ),
            )
            all_ok = False
        elif native_state == "ambiguous":
            item.update(
                status="action_needed",
                reason=native_error
                or "customized or malformed native block preserved; resolve manually",
            )
            all_ok = False
        installed = home / "plugins" / PLUGIN_NAME
        item["installed_path"] = str(installed)
        installed_sha, installed_error = _installed_revision(runner, installed)
        item["installed_revision"] = installed_sha
        item["source_revision"] = source_sha
        if installed_error:
            item.update(status="action_needed", reason=installed_error)
            all_ok = False
        elif item["status"] == "unchecked":
            item["status"] = "preflight_ok"
            item["plugin"] = (
                "absent"
                if installed_sha is None
                else ("current" if installed_sha == source_sha else "upgrade_required")
            )
        results.append(item)
    return results, all_ok


def _check_profile(
    runner: Runner, hermes: Sequence[str], item: dict[str, Any], source_sha: str
) -> None:
    profile = item["profile"]
    installed = Path(item["installed_path"])
    if not installed.exists():
        item.update(status="setup_required", plugin="absent", reason="plugin is not installed")
        return
    if item.get("installed_revision") != source_sha:
        item.update(
            status="setup_required", plugin="stale", reason="installed plugin revision differs"
        )
        return
    doctor = _hermes(runner, hermes, profile, "plugins", "doctor", str(installed), "--ci")
    if doctor.returncode != 0:
        item.update(
            status="setup_required", reason=f"installed plugin Doctor failed: {_reason(doctor)}"
        )
        return
    enabled, error = _plugins_enabled(
        _hermes(runner, hermes, profile, "config", "get", "plugins", "--json")
    )
    if error or not enabled:
        item.update(
            status="setup_required", plugin="disabled", reason=error or "plugin is not enabled"
        )
        return
    wait, error = _read_wait(_hermes(runner, hermes, profile, "config", "get", WAIT_KEY, "--json"))
    if error:
        item.update(status="setup_required", reason=error)
        return
    item["wait_for_all"] = wait
    payload, error = _read_native(
        _hermes(runner, hermes, profile, "session-coord", "native-check", "--json"),
        require_wait_true=True,
    )
    if error or payload is None:
        item.update(status="setup_required", reason=error or "native-check failed")
        return
    item.update(
        plugin="enabled",
        native_supported=True,
        surfaces=payload["surfaces"],
        activation=payload["activation"],
    )
    if wait is not True:
        item.update(status="setup_required", reason=f"{WAIT_KEY} is false")
    elif item["native_enrollment"] != "current":
        item.update(status="setup_required", reason="native managed enrollment is absent or stale")
    else:
        item.update(
            status="configured_restart_required",
            reason="configured on disk; fresh Hermes processes only",
            restart_required=True,
        )


def _setup_profile(
    runner: Runner,
    hermes: Sequence[str],
    item: dict[str, Any],
    plugin: Path,
    source_sha: str,
) -> str | None:
    """Apply sanctioned operations for one profile; return an error if partial."""
    item["_mutation_performed"] = False
    profile = item["profile"]
    installed = Path(item["installed_path"])
    if item.get("installed_revision") != source_sha:
        command = ["plugins", "install", _path_uri(plugin), "--ref", source_sha, "--no-enable"]
        if installed.exists():
            command.append("--force")
        item["_mutation_performed"] = True
        result = _hermes(runner, hermes, profile, *command)
        if result.returncode != 0:
            return f"plugin install failed or is uncertain: {_reason(result)}"
        item["plugin"] = "installed"
    installed_sha, installed_error = _installed_revision(runner, installed)
    if installed_error or installed_sha != source_sha:
        return installed_error or "installed plugin revision does not match the supplied source"
    item["installed_revision"] = installed_sha
    doctor = _hermes(runner, hermes, profile, "plugins", "doctor", str(installed), "--ci")
    if doctor.returncode != 0:
        return f"installed plugin Doctor failed: {_reason(doctor)}"
    enabled, error = _plugins_enabled(
        _hermes(runner, hermes, profile, "config", "get", "plugins", "--json")
    )
    if error:
        return error
    if not enabled:
        item["_mutation_performed"] = True
        enable = _hermes(
            runner,
            hermes,
            profile,
            "plugins",
            "enable",
            PLUGIN_NAME,
            "--no-allow-tool-override",
        )
        if enable.returncode != 0:
            return f"plugin enable failed or is uncertain: {_reason(enable)}"
        enabled, error = _plugins_enabled(
            _hermes(runner, hermes, profile, "config", "get", "plugins", "--json")
        )
        if error or not enabled:
            return error or "plugin enable did not persist"
    item["plugin"] = "enabled"
    preliminary, error = _read_native(
        _hermes(runner, hermes, profile, "session-coord", "native-check", "--json"),
        require_wait_true=False,
    )
    if error or preliminary is None:
        return error or "native-check failed after enable"
    if preliminary["wait_for_all"] is not True:
        item["_mutation_performed"] = True
        setting = _hermes(runner, hermes, profile, "config", "set", WAIT_KEY, "true")
        if setting.returncode != 0:
            return f"config set failed or is uncertain: {_reason(setting)}"
    wait, error = _read_wait(_hermes(runner, hermes, profile, "config", "get", WAIT_KEY, "--json"))
    if error or wait is not True:
        return error or f"{WAIT_KEY} readback is false"
    final, error = _read_native(
        _hermes(runner, hermes, profile, "session-coord", "native-check", "--json"),
        require_wait_true=True,
    )
    if error or final is None:
        return error or "final native-check failed"
    item.update(
        wait_for_all=True,
        native_supported=True,
        surfaces=final["surfaces"],
        activation=final["activation"],
    )
    return None


def _watchdog_payload(
    result: CommandResult, label: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = _json_object(result.stdout, label)
    except JsonError as exc:
        return None, str(exc)
    if "ok" in payload and type(payload["ok"]) is not bool:
        return None, f"{label} ok must be a Boolean"
    return payload, None


def _watchdog_check(
    runner: Runner, hermes: Sequence[str]
) -> tuple[CommandResult, dict[str, Any] | None, str | None]:
    result = _hermes(
        runner, hermes, "default", "session-coord", "watchdog-setup", "--json", "--check"
    )
    payload, error = _watchdog_payload(result, "watchdog check")
    if error:
        return result, None, error
    if result.returncode == 0 and payload is not None and payload.get("ok") is True:
        return result, payload, None
    return (
        result,
        payload,
        str((payload or {}).get("reason") or (payload or {}).get("status") or _reason(result)),
    )


def _setup_watchdog(runner: Runner, hermes: Sequence[str]) -> tuple[dict[str, Any], bool]:
    initial, initial_payload, error = _watchdog_check(runner, hermes)
    if error is None:
        return {
            "requested": True,
            "status": "configured",
            "details": initial_payload,
            "mutation_performed": False,
        }, True
    safe_missing = bool(
        initial_payload and initial_payload.get("status") in {"absent", "missing", "setup_required"}
    )
    if initial.returncode not in (0, 1) or not safe_missing:
        return {
            "requested": True,
            "status": "action_needed",
            "reason": f"watchdog preflight was not a proven missing state: {error}",
            "details": initial_payload,
            "mutation_performed": False,
        }, False
    setup = _hermes(runner, hermes, "default", "session-coord", "watchdog-setup", "--json")
    setup_payload, setup_error = _watchdog_payload(setup, "watchdog setup")
    observed, observed_payload, observed_error = _watchdog_check(runner, hermes)
    if (
        setup.returncode == 0
        and setup_error is None
        and setup_payload is not None
        and setup_payload.get("ok") is True
        and observed.returncode == 0
        and observed_error is None
    ):
        return {
            "requested": True,
            "status": "configured",
            "details": observed_payload,
            "mutation_performed": True,
        }, True
    return {
        "requested": True,
        "status": "partial",
        "reason": setup_error or _reason(setup),
        "setup_details": setup_payload,
        "observed": observed_payload,
        "recovery": (
            "run this helper in check mode; do not create a second job until status is known"
        ),
        "mutation_performed": True,
    }, False


def _discover_profiles(
    runner: Runner, hermes: Sequence[str]
) -> tuple[list[str] | None, str | None]:
    home, error = _resolve_home(runner, hermes, "default")
    if error or home is None:
        return None, error or "default profile unavailable"
    names = ["default"]
    root = home / "profiles"
    if root.is_dir():
        names.extend(
            sorted(
                path.name
                for path in root.iterdir()
                if path.is_dir()
                and (path / "config.yaml").is_file()
                and PROFILE_RE.fullmatch(path.name)
            )
        )
    return names, None


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _emit(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Hermes native setup: {report['mode']} — {'READY' if report['ok'] else 'NOT READY'}")
    for item in report["profiles"]:
        print(f"  {item['profile']}: {item['status']}")
        if item.get("reason"):
            print(f"    {item['reason']}")
    watchdog = report.get("watchdog")
    if watchdog and watchdog.get("requested"):
        print(f"  watchdog (shared/default): {watchdog['status']}")
        if watchdog.get("reason"):
            print(f"    {watchdog['reason']}")
    if report.get("restart_required"):
        print("Configured on disk. A safe restart is still required; no process was restarted.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("check", "setup", "remove"),
        help="check, explicit setup, or plugin-only removal",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--profile", action="append", default=[], help="profile to check/setup; repeatable"
    )
    selection.add_argument(
        "--all-profiles",
        action="store_true",
        help="explicitly select default and every named profile",
    )
    parser.add_argument(
        "--plugin-path",
        type=Path,
        help="standalone session-coord-native Git checkout",
    )
    parser.add_argument(
        "--watchdog",
        action="store_true",
        help="also check/setup one shared watchdog through profile default",
    )
    parser.add_argument(
        "--hermes", default="hermes", help="Hermes executable path; passed as one argv token"
    )
    parser.add_argument(
        "--hermes-arg",
        action="append",
        default=[],
        help="extra Hermes launcher argv token; repeat for a Python/script launcher",
    )
    parser.add_argument(
        "--check", action="store_true", help="preview plugin-only removal without mutation"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    return parser


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profiles = _dedupe(args.profile)
    for profile in profiles:
        if profile != "default" and PROFILE_RE.fullmatch(profile) is None:
            parser.error(f"invalid profile name: {profile!r}")
    if args.watchdog and not args.all_profiles and "default" not in profiles:
        parser.error("--watchdog requires selecting profile default (or --all-profiles)")
    if args.mode == "remove" and args.watchdog:
        parser.error("plugin-only removal preserves the shared watchdog; omit --watchdog")
    if args.mode != "remove" and args.check:
        parser.error("--check applies to remove; use mode check for setup inspection")
    plugin = args.plugin_path.expanduser().resolve() if args.plugin_path else None
    if args.mode != "remove" and (plugin is None or not plugin.is_dir()):
        parser.error("--plugin-path must be an existing directory for check/setup")
    run = runner or Runner()
    hermes_command = [args.hermes, *args.hermes_arg]
    if args.all_profiles:
        discovered, error = _discover_profiles(run, hermes_command)
        if error or discovered is None:
            report = {
                "mode": args.mode,
                "ok": False,
                "profiles": [],
                "restart_required": False,
                "reason": error or "profile discovery failed",
                "watchdog": {"requested": args.watchdog, "status": "not_checked"},
            }
            _emit(report, args.json)
            return 1
        profiles = discovered
    if not profiles:
        parser.error("select at least one --profile or use --all-profiles")
    if args.mode == "remove":
        from hermes_remove import remove_profiles

        report = remove_profiles(run, hermes_command, profiles, dry=args.check)
        _emit(report, args.json)
        if not args.json:
            print("Preserved: " + report["preserved"])
            for item in report["profiles"]:
                if item.get("backup"):
                    print(f"  {item['profile']} plugin recovery copy: {item['backup']}")
            print(report["note"])
        return 0 if report["ok"] else 1
    if plugin is None:
        print("--plugin-path is required for check/setup", file=sys.stderr)
        return 2
    _plugin_name, identity_error = _source_plugin_name(plugin)
    if identity_error:
        report = {
            "mode": args.mode,
            "ok": False,
            "profiles": [_profile_result(profile) for profile in profiles],
            "restart_required": False,
            "reason": identity_error,
            "watchdog": {"requested": args.watchdog, "status": "not_checked"},
        }
        _emit(report, args.json)
        return 1
    source_sha, source_error = _source_revision(run, plugin)
    if source_error or source_sha is None:
        report = {
            "mode": args.mode,
            "ok": False,
            "profiles": [_profile_result(profile) for profile in profiles],
            "restart_required": False,
            "reason": source_error,
            "watchdog": {"requested": args.watchdog, "status": "not_checked"},
        }
        _emit(report, args.json)
        return 1
    source_clean, source_clean_error = _source_clean(run, plugin)
    if source_clean_error or source_clean is None:
        report = {
            "mode": args.mode,
            "ok": False,
            "profiles": [_profile_result(profile) for profile in profiles],
            "restart_required": False,
            "reason": source_clean_error,
            "watchdog": {"requested": args.watchdog, "status": "not_checked"},
        }
        _emit(report, args.json)
        return 1
    default_home, error = _resolve_home(run, hermes_command, "default")
    if error or default_home is None:
        report = {
            "mode": args.mode,
            "ok": False,
            "profiles": [],
            "restart_required": False,
            "reason": error or "cannot resolve canonical board path",
            "watchdog": {"requested": args.watchdog, "status": "not_checked"},
        }
        _emit(report, args.json)
        return 1
    coord = default_home / "scripts" / "session_coord.py"
    if not coord.is_file():
        report = {
            "mode": args.mode,
            "ok": False,
            "profiles": [_profile_result(profile) for profile in profiles],
            "restart_required": False,
            "reason": (
                f"board CLI not found at {coord}; run install.py with the default destination"
            ),
            "watchdog": {"requested": args.watchdog, "status": "not_checked"},
        }
        _emit(report, args.json)
        return 1
    results, preflight_ok = _preflight(run, hermes_command, profiles, plugin, coord, source_sha)
    if not source_clean:
        for item in results:
            if item["status"] == "preflight_ok":
                item.update(
                    status="source_snapshot_required",
                    reason=(
                        "plugin Doctor examined uncommitted working-tree bytes, but "
                        "pinned installation can install only a commit; use a clean "
                        "committed snapshot"
                    ),
                )
        preflight_ok = False
    watchdog: dict[str, Any] = {"requested": args.watchdog, "status": "not_requested"}
    if args.mode == "check":
        if preflight_ok:
            for item in results:
                _check_profile(run, hermes_command, item, source_sha)
        if args.watchdog and preflight_ok:
            _result, details, error = _watchdog_check(run, hermes_command)
            watchdog = {
                "requested": True,
                "status": "configured" if error is None else "setup_required",
                "reason": error or "",
                "details": details,
            }
        ok = preflight_ok and all(
            item["status"] == "configured_restart_required" for item in results
        )
        ok = ok and (not args.watchdog or watchdog["status"] == "configured")
        report = {
            "mode": "check",
            "ok": ok,
            "source": {"revision": source_sha, "clean": source_clean},
            "profiles": results,
            "watchdog": watchdog,
            "restart_required": bool(ok),
            "mutation_performed": False,
        }
        _emit(report, args.json)
        return 0 if ok else 1
    if not preflight_ok:
        report = {
            "mode": "setup",
            "ok": False,
            "source": {"revision": source_sha, "clean": source_clean},
            "profiles": results,
            "watchdog": watchdog,
            "restart_required": False,
            "mutation_performed": False,
            "reason": "preflight failed for one or more profiles; no setup mutation was attempted",
        }
        _emit(report, args.json)
        return 1
    mutation_attempted = False
    setup_ok = True
    for item in results:
        error = _setup_profile(run, hermes_command, item, plugin, source_sha)
        mutation_attempted = mutation_attempted or item.pop("_mutation_performed", False)
        if error:
            item.update(status="partial", reason=error)
            setup_ok = False
            break
        item.update(status="configured_pending_enrollment", reason="")
    if setup_ok and args.watchdog:
        watchdog, setup_ok = _setup_watchdog(run, hermes_command)
        mutation_attempted = mutation_attempted or watchdog.get("mutation_performed", False)
    if setup_ok:
        for item in results:
            expected = Path(item["carrier"])
            actual, actual_kind = _carrier(item["profile"], Path(item["profile_home"]))
            if actual != expected or actual_kind != item["carrier_kind"]:
                state, detail = (
                    "failed",
                    "profile carrier changed after preflight; native block not written",
                )
            else:
                state, detail = _atomic_enroll(
                    expected,
                    item["profile"],
                    coord,
                    item["carrier_kind"],
                    item["carrier_digest"],
                )
            mutation_attempted = mutation_attempted or "written" in detail
            item["native_enrollment"] = state
            if state != "current":
                item.update(status="partial", reason=f"native enrollment failed: {detail}")
                setup_ok = False
                break
            item.update(
                status="configured_restart_required",
                reason="configured on disk; fresh Hermes processes only",
                restart_required=True,
            )
    if not setup_ok:
        for item in results:
            if item["status"] == "configured_pending_enrollment":
                item.update(
                    status="configured_not_enrolled",
                    reason=(
                        "Hermes configuration persisted, but global setup did not "
                        "complete; rerun after recovery"
                    ),
                )
    report = {
        "mode": "setup",
        "ok": setup_ok,
        "source": {"revision": source_sha, "clean": source_clean},
        "profiles": results,
        "watchdog": watchdog,
        "restart_required": bool(setup_ok),
        "mutation_performed": mutation_attempted,
    }
    _emit(report, args.json)
    return 0 if setup_ok else 1


if __name__ == "__main__":
    sys.exit(main())
