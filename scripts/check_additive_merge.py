#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""Additive-only merge gate for routertl-tool-index/catalog.json.

Compares the current catalog.json against a baseline (default: ``main``) and
blocks:

  * Removals — an entry present in baseline that vanished from HEAD.
  * Status downgrades — active/alpha/beta → deprecated/non-redistributable.

These actions break downstream ``rr tool install`` callers and need an
explicit, signed-off migration. Override by including a line beginning with
``MIGRATION:`` in the most recent commit message, or by setting the
``ALLOW_BREAKING_MERGE=1`` env var (CI use).

New entries, edits, version bumps, and *upgrades* of status (deprecated →
active) are always allowed.

Usage::

    python scripts/check_additive_merge.py
    python scripts/check_additive_merge.py --baseline origin/main
    python scripts/check_additive_merge.py --baseline-file old-catalog.json

Exit code: 0 if clean; 1 if blocked changes without override.

RTL-P2.491 (additive-only merge gate).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "catalog.json"

_OK_STATUSES = ("active", "alpha", "beta")
_DOWNGRADED_STATUSES = ("deprecated", "non-redistributable")


def _load_catalog_from_text(text: str) -> dict[tuple[str, str], dict]:
    if not text.strip():
        return {}
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("catalog.json must be a JSON array")
    out: dict[tuple[str, str], dict] = {}
    for entry in raw:
        out[(entry["namespace"], entry["name"])] = entry
    return out


def _git_show(ref: str, path: str) -> str:
    """Return the contents of ``path`` at ``ref``, or '' if missing."""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _last_commit_message() -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def _migration_override_present() -> tuple[bool, str]:
    if os.environ.get("ALLOW_BREAKING_MERGE") == "1":
        return True, "ALLOW_BREAKING_MERGE=1"
    msg = _last_commit_message()
    for line in msg.splitlines():
        stripped = line.strip()
        if stripped.startswith("MIGRATION:") and len(stripped) > len("MIGRATION:"):
            return True, line.strip()
    return False, ""


def diff_catalogs(
    old: dict[tuple[str, str], dict],
    new: dict[tuple[str, str], dict],
) -> list[str]:
    """Return a list of human-readable blocking-change messages."""
    blocked: list[str] = []
    for key in sorted(old):
        ns, name = key
        slug = f"{ns}/{name}"
        if key not in new:
            blocked.append(f"REMOVED: '{slug}' was in baseline; missing from HEAD")
            continue
        old_status = old[key].get("status", "active")
        new_status = new[key].get("status", "active")
        if old_status in _OK_STATUSES and new_status in _DOWNGRADED_STATUSES:
            blocked.append(
                f"STATUS DOWNGRADE: '{slug}' {old_status} → {new_status}"
            )
    return blocked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        default="main",
        help="git ref to compare against (default: main).",
    )
    parser.add_argument(
        "--baseline-file",
        type=Path,
        help="Compare against this catalog.json file instead of a git ref.",
    )
    args = parser.parse_args()

    if args.baseline_file:
        old_text = args.baseline_file.read_text() if args.baseline_file.exists() else ""
    else:
        old_text = _git_show(args.baseline, "catalog.json")

    new_text = CATALOG_PATH.read_text() if CATALOG_PATH.exists() else ""

    try:
        old = _load_catalog_from_text(old_text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"WARNING: baseline catalog unparseable ({e}); skipping gate.")
        return 0
    try:
        new = _load_catalog_from_text(new_text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: HEAD catalog.json unparseable: {e}", file=sys.stderr)
        return 1

    if not old:
        print("OK: no baseline catalog (likely first commit) — gate skipped.")
        return 0

    blocked = diff_catalogs(old, new)
    if not blocked:
        print(f"OK: additive-only merge gate passed ({len(new)} tool(s) in HEAD).")
        return 0

    override_ok, override_reason = _migration_override_present()
    if override_ok:
        print("Blocking changes detected, but override accepted:")
        for b in blocked:
            print(f"  - {b}")
        print(f"Override: {override_reason}")
        return 0

    print("Additive-only merge gate FAILED:", file=sys.stderr)
    for b in blocked:
        print(f"  - {b}", file=sys.stderr)
    print(
        "\nResolve by either:\n"
        "  1. Re-adding the removed entry / restoring the prior status, OR\n"
        "  2. Adding a 'MIGRATION: <reason>' line to the commit message\n"
        "     (or setting ALLOW_BREAKING_MERGE=1 in CI when justified).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
