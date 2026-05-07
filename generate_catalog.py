#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""Regenerate catalog.json from registry.yml + per-tool tool.yml manifests.

Reads `registry.yml` as the canonical curated list (locked design decision,
RTL-P2.486). For each `<namespace>/<name>` entry, loads
`tools/<namespace>/<name>/tool.yml`, validates the schema, and rolls all
entries into a deterministic, sorted `catalog.json`.

Usage::

    python generate_catalog.py            # regenerate, exit 1 on schema error
    python generate_catalog.py --check    # dry-run; verify catalog.json is up
                                          # to date and schema-clean (CI)

Schema (RTL-P2.486 design memo):
  Required: name, namespace, description, license, version, install
  install: must declare exactly one of pypi:|git:|wheel:
  Optional: pairs_with, vendor_compat, tags, interfaces, pairs_with_ips,
            status, maintainer, homepage, extras

RTL-P2.486 / RTL-P2.491 (catalog generator + additive-merge gate).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent
TOOLS_DIR = REPO_ROOT / "tools"
REGISTRY_YML = REPO_ROOT / "registry.yml"
CATALOG_OUT = REPO_ROOT / "catalog.json"

_REQUIRED_FIELDS = ("name", "namespace", "description", "license", "version", "install")
_INSTALL_CHANNELS = ("pypi", "git", "wheel")
_VALID_STATUSES = ("active", "alpha", "beta", "deprecated", "non-redistributable")


class SchemaError(Exception):
    """Raised when registry.yml or a tool.yml fails validation."""


def _validate_install(install: dict, where: str) -> list[str]:
    """Return a list of human-readable schema errors for the install: block."""
    errors: list[str] = []
    if not isinstance(install, dict):
        return [f"{where}.install: must be a mapping"]
    channels_present = [c for c in _INSTALL_CHANNELS if c in install]
    if len(channels_present) == 0:
        errors.append(
            f"{where}.install: must declare exactly one of "
            f"{'/'.join(_INSTALL_CHANNELS)}"
        )
    elif len(channels_present) > 1:
        errors.append(
            f"{where}.install: declares multiple channels "
            f"({', '.join(channels_present)}); pick one"
        )
    for key in ("version", "ref"):
        if key in install and not isinstance(install[key], str):
            errors.append(f"{where}.install.{key}: must be a string")
    if "extras" in install and not isinstance(install["extras"], list):
        errors.append(f"{where}.install.extras: must be a list of strings")
    return errors


def validate_tool_yml(meta: dict, ns: str, name: str) -> list[str]:
    """Return schema errors for one tool.yml entry. Empty list = clean."""
    where = f"tools/{ns}/{name}/tool.yml"
    if not isinstance(meta, dict):
        return [f"{where}: top-level must be a YAML mapping"]
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in meta:
            errors.append(f"{where}: missing required field '{field}'")
    if meta.get("namespace") and meta["namespace"] != ns:
        errors.append(
            f"{where}: namespace field '{meta['namespace']}' "
            f"disagrees with directory layout '{ns}'"
        )
    if meta.get("name") and meta["name"] != name:
        errors.append(
            f"{where}: name field '{meta['name']}' "
            f"disagrees with directory layout '{name}'"
        )
    status = meta.get("status", "active")
    if status not in _VALID_STATUSES:
        errors.append(
            f"{where}.status: '{status}' not in {_VALID_STATUSES}"
        )
    install = meta.get("install")
    if install is not None:
        errors.extend(_validate_install(install, where))
    return errors


def _coerce_install_summary(install: dict) -> dict:
    """Reduce the install: block to the catalog summary shape."""
    out: dict = {}
    if not isinstance(install, dict):
        return out
    for channel in _INSTALL_CHANNELS:
        if channel in install:
            out["channel"] = channel
            out["package"] = install[channel]
            break
    if install.get("ref"):
        out["ref"] = install["ref"]
    if install.get("version"):
        out["version_spec"] = install["version"]
    if install.get("extras"):
        out["extras"] = list(install["extras"])
    return out


def _load_curated_list() -> list[tuple[str, str]]:
    """Return [(namespace, name), …] from registry.yml. Errors out if missing."""
    if not REGISTRY_YML.exists():
        raise SchemaError(f"{REGISTRY_YML.name} not found")
    data = yaml.safe_load(REGISTRY_YML.read_text()) or {}
    raw = data.get("tools") or []
    if not isinstance(raw, list):
        raise SchemaError("registry.yml: 'tools' must be a list of '<ns>/<name>' strings")
    out: list[tuple[str, str]] = []
    for slug in raw:
        if not isinstance(slug, str) or "/" not in slug:
            raise SchemaError(
                f"registry.yml: entry {slug!r} is not a '<ns>/<name>' string"
            )
        ns, name = slug.split("/", 1)
        out.append((ns, name))
    return out


def build_catalog() -> tuple[list[dict], list[str]]:
    """Walk registry.yml, validate every tool.yml, return (catalog, errors)."""
    errors: list[str] = []
    try:
        curated = _load_curated_list()
    except SchemaError as e:
        return [], [str(e)]

    catalog: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for ns, name in curated:
        if (ns, name) in seen:
            errors.append(f"registry.yml: duplicate entry '{ns}/{name}'")
            continue
        seen.add((ns, name))

        tool_yml = TOOLS_DIR / ns / name / "tool.yml"
        if not tool_yml.is_file():
            errors.append(
                f"registry.yml lists '{ns}/{name}' but "
                f"{tool_yml.relative_to(REPO_ROOT)} is missing"
            )
            continue

        meta = yaml.safe_load(tool_yml.read_text()) or {}
        entry_errors = validate_tool_yml(meta, ns, name)
        if entry_errors:
            errors.extend(entry_errors)
            continue

        catalog.append({
            "namespace":      ns,
            "name":           name,
            "version":        meta.get("version", ""),
            "description":    meta.get("description", ""),
            "license":        meta.get("license", ""),
            "homepage":       meta.get("homepage", ""),
            "maintainer":     meta.get("maintainer", ""),
            "status":         meta.get("status", "active"),
            "install":        _coerce_install_summary(meta.get("install") or {}),
            "pairs_with":     list(meta.get("pairs_with") or []),
            "vendor_compat":  list(meta.get("vendor_compat") or []),
            "interfaces":     list(meta.get("interfaces") or []),
            "tags":           list(meta.get("tags") or []),
            "pairs_with_ips": list(meta.get("pairs_with_ips") or []),
        })

    orphan_dirs: list[str] = []
    if TOOLS_DIR.exists():
        for ns_dir in sorted(TOOLS_DIR.iterdir()):
            if not ns_dir.is_dir():
                continue
            for name_dir in sorted(ns_dir.iterdir()):
                if not name_dir.is_dir():
                    continue
                if (
                    (ns_dir.name, name_dir.name) not in seen
                    and (name_dir / "tool.yml").is_file()
                ):
                    orphan_dirs.append(f"tools/{ns_dir.name}/{name_dir.name}/")
    if orphan_dirs:
        errors.append(
            "Orphan tool.yml(s) on disk not in registry.yml: "
            + ", ".join(orphan_dirs)
            + " — either add to registry.yml or delete the directory"
        )

    catalog.sort(key=lambda e: (e["namespace"], e["name"]))
    return catalog, errors


def _serialize(catalog: list[dict]) -> str:
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: verify catalog.json is up to date and schema-clean. "
             "Exits non-zero on drift or schema error (CI).",
    )
    args = parser.parse_args()

    catalog, errors = build_catalog()
    if errors:
        print("Schema validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    new_text = _serialize(catalog)
    if args.check:
        old_text = CATALOG_OUT.read_text() if CATALOG_OUT.exists() else ""
        if old_text != new_text:
            print(
                "catalog.json is stale — re-run `python generate_catalog.py` "
                "and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: catalog.json up to date ({len(catalog)} tool(s)).")
        return 0

    CATALOG_OUT.write_text(new_text)
    print(f"Generated {CATALOG_OUT.name} with {len(catalog)} companion tool(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
