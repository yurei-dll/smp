#!/usr/bin/env python3
"""Normalize and classify JSON mod lists exported by Prism Launcher."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


GROUPS = (
    "core",
    "client-required",
    "client-optional",
    "server-required",
    "server-curated",
)
OVERRIDE_GROUPS = (*GROUPS, "ignored")
MODRINTH_PATH = re.compile(r"^/(?:mod|project)/([^/]+)/?$")


class ImportErrorDetail(ValueError):
    """An error that should be presented directly to the user."""


@dataclass(frozen=True)
class Mod:
    filename: str
    name: str
    url: str
    version: str
    source: str | None
    project_id: str | None

    @property
    def identity(self) -> str:
        if self.source and self.project_id:
            return f"{self.source}:{self.project_id.casefold()}"
        return f"filename:{self.filename.casefold()}"

    def selectors(self) -> set[str]:
        values = {self.filename.casefold(), self.name.casefold()}
        if self.project_id:
            values.add(self.project_id.casefold())
        return values

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "filename": self.filename,
            "name": self.name,
            "url": self.url,
            "version": self.version,
        }
        if self.source:
            result["source"] = self.source
        if self.project_id:
            result["project_id"] = self.project_id
        return result


def _required_string(entry: dict[str, Any], field: str, location: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ImportErrorDetail(f"{location}: {field!r} must be a non-empty string")
    return value.strip()


def platform_identity(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host in {"modrinth.com", "www.modrinth.com"}:
        match = MODRINTH_PATH.match(parsed.path)
        if match:
            return "modrinth", match.group(1)
    return None, None


def parse_mod(entry: Any, location: str) -> Mod:
    if not isinstance(entry, dict):
        raise ImportErrorDetail(f"{location}: expected an object")
    filename = _required_string(entry, "filename", location)
    name = _required_string(entry, "name", location)
    url = _required_string(entry, "url", location)
    version = _required_string(entry, "version", location)
    source, project_id = platform_identity(url)
    return Mod(filename, name, url, version, source, project_id)


def load_prism_lists(paths: Iterable[Path]) -> list[Mod]:
    mods: dict[str, Mod] = {}
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ImportErrorDetail(f"input does not exist: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ImportErrorDetail(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
        if not isinstance(raw, list):
            raise ImportErrorDetail(f"{path}: top-level JSON value must be a list")
        for index, entry in enumerate(raw):
            mod = parse_mod(entry, f"{path}[{index}]")
            previous = mods.get(mod.identity)
            if previous and previous != mod:
                raise ImportErrorDetail(
                    f"conflicting entries for {mod.identity}: "
                    f"{previous.filename!r} and {mod.filename!r}"
                )
            mods[mod.identity] = mod
    return sorted(mods.values(), key=lambda mod: (mod.name.casefold(), mod.filename.casefold()))


def load_overrides(path: Path) -> dict[str, set[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportErrorDetail(f"overrides file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImportErrorDetail(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ImportErrorDetail(f"{path}: top-level JSON value must be an object")

    unknown = set(raw) - set(OVERRIDE_GROUPS)
    if unknown:
        raise ImportErrorDetail(f"{path}: unknown override groups: {', '.join(sorted(unknown))}")

    overrides: dict[str, set[str]] = {}
    claimed: dict[str, str] = {}
    for group in OVERRIDE_GROUPS:
        values = raw.get(group, [])
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ImportErrorDetail(f"{path}: {group!r} must be a list of non-empty strings")
        normalized = {value.strip().casefold() for value in values}
        for selector in normalized:
            if selector in claimed:
                raise ImportErrorDetail(
                    f"{path}: selector {selector!r} appears in both {claimed[selector]!r} and {group!r}"
                )
            claimed[selector] = group
        overrides[group] = normalized
    return overrides


def classify(mods: Iterable[Mod], overrides: dict[str, set[str]]) -> tuple[dict[str, list[Mod]], list[Mod]]:
    categorized = {group: [] for group in GROUPS}
    review: list[Mod] = []
    for mod in mods:
        matched = [group for group in OVERRIDE_GROUPS if mod.selectors() & overrides[group]]
        if len(matched) > 1:
            raise ImportErrorDetail(
                f"{mod.name!r} matches selectors in multiple groups: {', '.join(matched)}"
            )
        if not matched:
            review.append(mod)
        elif matched[0] != "ignored":
            categorized[matched[0]].append(mod)
    return categorized, review


def write_outputs(output_dir: Path, categorized: dict[str, list[Mod]], review: list[Mod]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {group: mods for group, mods in categorized.items()}
    payloads["review"] = review
    for group, mods in payloads.items():
        target = output_dir / f"{group}.json"
        content = [mod.as_dict() for mod in mods]
        target.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "counts": {group: len(mods) for group, mods in payloads.items()},
        "total_included": sum(len(mods) for mods in categorized.values()),
        "total_review": len(review),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("inputs", nargs="+", type=Path, help="Prism JSON mod list(s)")
    result.add_argument(
        "--overrides",
        type=Path,
        default=repo_root / "pack/classification-overrides.json",
        help="persistent classification overrides",
    )
    result.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "pack/catalog",
        help="directory for categorized JSON lists",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        mods = load_prism_lists(args.inputs)
        overrides = load_overrides(args.overrides)
        categorized, review = classify(mods, overrides)
        write_outputs(args.output_dir, categorized, review)
    except (ImportErrorDetail, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    included = sum(len(entries) for entries in categorized.values())
    print(f"Imported {len(mods)} unique mods: {included} classified, {len(review)} need review")
    print(f"Wrote categorized lists to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

