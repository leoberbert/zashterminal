#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGETS = (
    (
        "src/zashterminal/settings/config.py",
        r'(APP_VERSION\s*=\s*")([^"]+)(")',
        "group3",
    ),
    (
        "locale/src/zashterminal/settings/config.py",
        r'(APP_VERSION\s*=\s*")([^"]+)(")',
        "group3",
    ),
    ("pyproject.toml", r'^(version\s*=\s*")([^"]+)(")\s*$', "group3"),
    ("locale/pyproject.toml", r'^(version\s*=\s*")([^"]+)(")\s*$', "group3"),
    ("default.nix", r'^(  version\s*=\s*")([^"]+)(";\s*)$', "group3"),
    ("locale/default.nix", r'^(  version\s*=\s*")([^"]+)(";\s*)$', "group3"),
    ("PKGBUILD", r"^(pkgver=)([^\n]+)$", "group2"),
)


def read_current_version() -> str:
    config_path = ROOT / "src/zashterminal/settings/config.py"
    content = config_path.read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', content)
    if not match:
        raise SystemExit(f"Unable to find APP_VERSION in {config_path}")
    return match.group(1)


def bump_version(version: str, part: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        raise SystemExit(
            f"Current version '{version}' is not semver-like (expected X.Y.Z)"
        )

    major, minor, patch = (int(piece) for piece in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"Unsupported bump type: {part}")


def _replace_match(match: re.Match[str], new_version: str, mode: str) -> str:
    if mode == "group2":
        return f"{match.group(1)}{new_version}"
    return f"{match.group(1)}{new_version}{match.group(3)}"


def replace_once(path: Path, pattern: str, new_version: str, mode: str) -> bool:
    text = path.read_text(encoding="utf-8")
    compiled = re.compile(pattern, re.MULTILINE)
    updated, count = compiled.subn(
        lambda match: _replace_match(match, new_version, mode), text, count=1
    )
    if count != 1:
        raise SystemExit(f"Unable to update version in {path}")

    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_all(new_version: str) -> list[str]:
    changed: list[str] = []
    for rel_path, pattern, mode in TARGETS:
        path = ROOT / rel_path
        if not path.exists():
            continue
        if replace_once(path, pattern, new_version, mode):
            changed.append(rel_path)
    return changed


def collect_mismatches(expected_version: str) -> list[str]:
    mismatches: list[str] = []
    for rel_path, pattern, _mode in TARGETS:
        path = ROOT / rel_path
        if not path.exists():
            continue

        text = path.read_text(encoding="utf-8")
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            mismatches.append(f"{rel_path}: version marker not found")
            continue

        current_value = match.group(2)
        if current_value != expected_version:
            mismatches.append(f"{rel_path}: {current_value} != {expected_version}")

    return mismatches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize Zashterminal version across project files."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--set", dest="set_version", help="Set an explicit version.")
    group.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="Increment the current semantic version.",
    )
    parser.add_argument(
        "--print-current",
        action="store_true",
        help="Print the current APP_VERSION from config.py.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if any tracked file is not aligned with APP_VERSION.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    current_version = read_current_version()

    if args.check and not args.set_version and not args.bump:
        mismatches = collect_mismatches(current_version)
        if mismatches:
            print(f"Version mismatch detected against APP_VERSION={current_version}:")
            for item in mismatches:
                print(item)
            return 1
        print(f"All tracked files are aligned with APP_VERSION={current_version}")
        return 0

    if args.print_current and not args.set_version and not args.bump:
        print(current_version)
        return 0

    if args.set_version:
        new_version = args.set_version.strip()
    elif args.bump:
        new_version = bump_version(current_version, args.bump)
    else:
        parser.error("Provide --print-current, --check, --set, or --bump.")

    changed = sync_all(new_version)
    print(new_version)
    if changed:
        print("Updated files:")
        for rel_path in changed:
            print(rel_path)
    else:
        print("No files changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
