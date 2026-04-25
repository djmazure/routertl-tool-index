#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""Regenerate catalog.json from every tools/<ns>/<name>/tool.yml manifest.

Run after adding or editing a tool.yml::

    python generate_catalog.py

Mirrors routertl-ref-index/generate_catalog.py but serialises the
companion-tool schema (single tool.yml per tool, not per-version files —
see RTL-P2.486 design memo, locked decision #2).

RTL-P2.486 / RTL-P2.491 (catalog generator + additive-merge gate).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

TOOLS_DIR = Path("tools")
CATALOG_OUT = Path("catalog.json")


def _coerce_install_summary(install: dict) -> dict:
    """Reduce the install: block to the catalog summary shape.

    Keeps only the fields a search/list view needs — full pin spec
    (caret/tilde expansion) is the API server's job, not the catalog's.
    """
    if not isinstance(install, dict):
        return {}
    out: dict = {}
    if "pypi" in install:
        out["channel"] = "pypi"
        out["package"] = install["pypi"]
    elif "git" in install:
        out["channel"] = "git"
        out["package"] = install["git"]
        if install.get("ref"):
            out["ref"] = install["ref"]
    if install.get("version"):
        out["version_spec"] = install["version"]
    if install.get("extras"):
        out["extras"] = list(install["extras"])
    return out


def build_catalog() -> list[dict]:
    """Walk tools/<ns>/<name>/tool.yml and roll into a flat catalog list."""
    catalog: list[dict] = []
    if not TOOLS_DIR.exists():
        return catalog

    for ns_dir in sorted(TOOLS_DIR.iterdir()):
        if not ns_dir.is_dir():
            continue
        ns = ns_dir.name

        for name_dir in sorted(ns_dir.iterdir()):
            if not name_dir.is_dir():
                continue
            name = name_dir.name

            tool_yml = name_dir / "tool.yml"
            if not tool_yml.is_file():
                continue

            with open(tool_yml) as f:
                meta = yaml.safe_load(f) or {}

            # tool.yml may declare its own namespace/name; the directory
            # layout is canonical, so we override here for catalog-side
            # consistency.
            entry = {
                "namespace":   ns,
                "name":        name,
                "version":     meta.get("version", ""),
                "description": meta.get("description", ""),
                "license":     meta.get("license", ""),
                "homepage":    meta.get("homepage", ""),
                "maintainer":  meta.get("maintainer", ""),
                "status":      meta.get("status", "active"),
                "install":     _coerce_install_summary(meta.get("install") or {}),
                "pairs_with":     list(meta.get("pairs_with") or []),
                "vendor_compat":  list(meta.get("vendor_compat") or []),
                "interfaces":     list(meta.get("interfaces") or []),
                "tags":           list(meta.get("tags") or []),
                "pairs_with_ips": list(meta.get("pairs_with_ips") or []),
            }
            catalog.append(entry)

    return catalog


if __name__ == "__main__":
    catalog = build_catalog()
    with open(CATALOG_OUT, "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"Generated {CATALOG_OUT} with {len(catalog)} companion tool(s).")
