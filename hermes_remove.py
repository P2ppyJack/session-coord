"""Remove only optional native integration through public Hermes operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import hermes_setup as setup


def _tree_signature(root):
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                result[relative] = ("link", os.readlink(path))
            elif path.is_file():
                result[relative] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                result[relative] = ("directory",)
    return result


def _plan_profile(runner, hermes, profile):
    home, error = setup._resolve_home(runner, hermes, profile)
    if error or home is None:
        raise ValueError(error or "could not resolve profile")
    target = home / "plugins" / setup.PLUGIN_NAME
    if target.is_symlink() or target.parent.resolve() != home / "plugins":
        raise ValueError("plugin path is redirected; preserve it and resolve manually")
    if target.exists():
        if not target.is_dir():
            raise ValueError("plugin path is not a directory")
        _, error = setup._source_plugin_name(target)
        if error:
            raise ValueError(error)
    carrier, kind = setup._carrier(profile, home)
    if carrier.is_symlink() or carrier.resolve(strict=False) != carrier:
        raise ValueError("enrollment carrier is redirected; resolve manually")
    original = carrier.read_bytes() if carrier.exists() else b""
    content = original.decode("utf-8")
    state, _ = setup._native_state(content, home / "scripts/session_coord.py", kind)
    if state == "ambiguous":
        raise ValueError("customized or malformed native enrollment preserved; resolve manually")
    replacement = original
    if state != "absent":
        start = content.index(setup.NATIVE_BEGIN)
        end = content.index(setup.NATIVE_END) + len(setup.NATIVE_END)
        replacement = (content[:start] + content[end:]).encode("utf-8")
    enabled, error = setup._plugins_enabled(
        setup._hermes(runner, hermes, profile, "config", "get", "plugins", "--json")
    )
    if error:
        raise ValueError(error)
    if enabled and not target.exists():
        raise ValueError(
            "plugin files are missing but its allow-list entry remains; resolve configuration first"
        )
    return {
        "profile": profile,
        "home": home,
        "target": target,
        "carrier": carrier,
        "original": original,
        "replacement": replacement,
        "enabled": enabled,
        "signature": _tree_signature(target) if target.exists() else None,
    }


def _apply_profile(runner, hermes, plan, report):
    profile = plan["profile"]
    target = plan["target"]
    carrier = plan["carrier"]
    if (carrier.read_bytes() if carrier.exists() else b"") != plan["original"]:
        raise ValueError("enrollment changed after preflight; no removal attempted")
    if plan["signature"] is not None:
        if (
            not target.is_dir()
            or target.is_symlink()
            or _tree_signature(target) != plan["signature"]
        ):
            raise ValueError("plugin changed after preflight; preserved current files")
        backups = plan["home"] / "state/session-coord-native-backups"
        backups.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="remove-", dir=backups)) / "plugin"
        shutil.copytree(target, backup, symlinks=True)
        if _tree_signature(backup) != plan["signature"]:
            raise ValueError("plugin changed during backup; removal abandoned")
        report["backup"] = str(backup)
        report["mutation_performed"] = True
        disabled = setup._hermes(runner, hermes, profile, "plugins", "disable", setup.PLUGIN_NAME)
        if disabled.returncode:
            raise ValueError("disable failed or is uncertain: " + setup._reason(disabled))
        enabled, error = setup._plugins_enabled(
            setup._hermes(runner, hermes, profile, "config", "get", "plugins", "--json")
        )
        if error or enabled:
            raise ValueError(error or "plugin disable did not persist")
    if plan["replacement"] != plan["original"]:
        backup = setup.board_installer.rewrite_carrier_if_unchanged(
            carrier, plan["original"], plan["replacement"]
        )
        if backup is None or carrier.read_bytes() != plan["replacement"]:
            raise ValueError("enrollment changed during removal; plugin files preserved")
        report["mutation_performed"] = True
        report["enrollment_backup"] = str(backup)
    if plan["signature"] is not None:
        if target.is_symlink() or _tree_signature(target) != plan["signature"]:
            raise ValueError("plugin changed before removal; current files preserved")
        removed = setup._hermes(runner, hermes, profile, "plugins", "remove", setup.PLUGIN_NAME)
        if removed.returncode or target.exists() or target.is_symlink():
            raise ValueError("plugin removal failed or is uncertain: " + setup._reason(removed))
    if (carrier.read_bytes() if carrier.exists() else b"") != plan["replacement"]:
        raise ValueError("native enrollment removal readback differs")
    report.update(
        status="removed" if report["mutation_performed"] else "already_absent",
        restart_required=report["mutation_performed"],
        plugin="absent",
    )


def remove_profiles(runner, hermes, profiles, *, dry=False):
    plans = []
    results = []
    for profile in profiles:
        item = {
            "profile": profile,
            "status": "unchecked",
            "reason": "",
            "mutation_performed": False,
            "restart_required": False,
        }
        results.append(item)
        try:
            plan = _plan_profile(runner, hermes, profile)
            plans.append(plan)
            item.update(
                status="would_remove"
                if plan["signature"] is not None or plan["original"] != plan["replacement"]
                else "already_absent"
            )
        except (OSError, UnicodeError, ValueError) as exc:
            item.update(status="action_needed", reason=str(exc))
    preflight_ok = len(plans) == len(profiles)
    if preflight_ok and not dry:
        for plan, item in zip(plans, results):
            try:
                _apply_profile(runner, hermes, plan, item)
            except (OSError, UnicodeError, ValueError) as exc:
                item.update(
                    status="partial" if item["mutation_performed"] else "action_needed",
                    reason=str(exc),
                )
                break
    acceptable = {"would_remove", "already_absent"} if dry else {"removed", "already_absent"}
    return {
        "mode": "remove-check" if dry else "remove",
        "ok": preflight_ok and all(item["status"] in acceptable for item in results),
        "profiles": results,
        "mutation_performed": any(item["mutation_performed"] for item in results),
        "restart_required": any(item["restart_required"] for item in results),
        "preserved": (
            'board files/database, claims, receipts, skills, watchdog and '
            'joined-delegation setting'
        ),
        "note": (
            'No running process was restarted. Close/restart existing Hermes '
            'processes to unload the plugin. Pending waits remain for manual '
            'recovery; they are not cancelled or replayed.'
        ),
    }
