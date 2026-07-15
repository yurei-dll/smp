#!/usr/bin/env python3
"""Inspect and classify the mods installed in a Prism Launcher instance."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shlex
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

try:
    from .mod_classifier import (
        Inspection,
        ModrinthClient,
        add_modrinth_evidence,
        inspect_mod,
        inspection_dict,
        propose,
    )
except ImportError:  # Direct execution through scripts/import-prism.
    from mod_classifier import (
        Inspection,
        ModrinthClient,
        add_modrinth_evidence,
        inspect_mod,
        inspection_dict,
        propose,
    )


GROUPS = (
    "core",
    "client",
    "server",
)
OVERRIDE_GROUPS = (*GROUPS, "ignored")


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
    declared_side: str | None = None
    platform_version_id: str | None = None
    download_url: str | None = None

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
        if self.platform_version_id:
            result["platform_version_id"] = self.platform_version_id
        if self.declared_side:
            result["declared_side"] = self.declared_side
        if self.download_url:
            result["download_url"] = self.download_url
        return result


def prism_roots(explicit_root: Path | None = None) -> list[Path]:
    if explicit_root:
        return [explicit_root.expanduser()]
    home = Path.home()
    candidates = [
        home / ".local/share/PrismLauncher",
        home / ".var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher",
        home / "snap/prismlauncher/common/PrismLauncher",
        home / "Library/Application Support/PrismLauncher",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "PrismLauncher")
    return candidates


def _instance_display_name(path: Path) -> str:
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(path / "instance.cfg", encoding="utf-8")
        return config.get("General", "name", fallback=path.name)
    except (OSError, configparser.Error):
        return path.name


def resolve_instance(name: str, explicit_root: Path | None = None) -> Path:
    wanted = name.casefold()
    matches: list[Path] = []
    searched: list[Path] = []
    for root in prism_roots(explicit_root):
        instances = root / "instances"
        searched.append(instances)
        if not instances.is_dir():
            continue
        for candidate in instances.iterdir():
            if not candidate.is_dir() or not (candidate / "instance.cfg").is_file():
                continue
            if candidate.name.casefold() == wanted or _instance_display_name(candidate).casefold() == wanted:
                matches.append(candidate)
    unique = sorted(set(matches))
    if not unique:
        locations = ", ".join(str(path) for path in searched)
        raise ImportErrorDetail(f"Prism instance {name!r} was not found under: {locations}")
    if len(unique) > 1:
        raise ImportErrorDetail(
            f"Prism instance name {name!r} is ambiguous: {', '.join(str(path) for path in unique)}"
        )
    return unique[0]


def _mod_from_packwiz(path: Path) -> Mod:
    try:
        metadata = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ImportErrorDetail(f"cannot parse Prism metadata {path}: {exc}") from exc
    filename = metadata.get("filename")
    name = metadata.get("name")
    if not isinstance(filename, str) or not filename:
        raise ImportErrorDetail(f"{path}: missing non-empty filename")
    if not isinstance(name, str) or not name:
        name = Path(filename).stem
    update = metadata.get("update")
    modrinth = update.get("modrinth") if isinstance(update, dict) else None
    project_id = modrinth.get("mod-id") if isinstance(modrinth, dict) else None
    version_id = modrinth.get("version") if isinstance(modrinth, dict) else None
    download = metadata.get("download")
    download_url = download.get("url", "") if isinstance(download, dict) else ""
    side = metadata.get("side")
    return Mod(
        filename=filename,
        name=name,
        url=f"https://modrinth.com/mod/{project_id}" if project_id else str(download_url),
        version=str(version_id or "unknown"),
        source="modrinth" if project_id else None,
        project_id=project_id if isinstance(project_id, str) else None,
        declared_side=side if side in {"client", "server", "both"} else None,
        platform_version_id=version_id if isinstance(version_id, str) else None,
        download_url=str(download_url) if download_url else None,
    )


def load_prism_instance(instance: Path) -> tuple[list[Mod], Path]:
    mods_dir = instance / "minecraft/mods"
    if not mods_dir.is_dir():
        raise ImportErrorDetail(f"Prism instance has no mods directory: {mods_dir}")
    mods: dict[str, Mod] = {}
    index_dir = mods_dir / ".index"
    if index_dir.is_dir():
        for metadata_path in sorted(index_dir.glob("*.pw.toml")):
            mod = _mod_from_packwiz(metadata_path)
            if (mods_dir / mod.filename).is_file():
                mods[mod.filename.casefold()] = mod

    for jar in sorted(mods_dir.glob("*.jar")):
        if jar.name.casefold() not in mods:
            mods[jar.name.casefold()] = Mod(
                filename=jar.name,
                name=jar.stem,
                url="",
                version="unknown",
                source=None,
                project_id=None,
            )
    result = sorted(mods.values(), key=lambda mod: (mod.name.casefold(), mod.filename.casefold()))
    return result, mods_dir


def apply_source_overrides(mods: list[Mod], path: Path) -> list[Mod]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportErrorDetail(f"source overrides file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImportErrorDetail(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ImportErrorDetail(f"{path}: top-level value must be an object")
    by_filename = {mod.filename.casefold(): mod for mod in mods}
    for filename, fields in raw.items():
        if not isinstance(filename, str) or not isinstance(fields, dict):
            raise ImportErrorDetail(f"{path}: source overrides must map filenames to objects")
        key = filename.casefold()
        mod = by_filename.get(key)
        if not mod:
            raise ImportErrorDetail(f"{path}: source override does not match an installed JAR: {filename}")
        allowed = {"name", "source", "project_id", "platform_version_id", "download_url"}
        unknown = set(fields) - allowed
        if unknown:
            raise ImportErrorDetail(f"{path}: {filename}: unknown fields: {', '.join(sorted(unknown))}")
        if not all(isinstance(value, str) and value.strip() for value in fields.values()):
            raise ImportErrorDetail(f"{path}: {filename}: all source values must be non-empty strings")
        by_filename[key] = replace(mod, **fields)
    return sorted(by_filename.values(), key=lambda mod: (mod.name.casefold(), mod.filename.casefold()))


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


def classify_with_evidence(
    mods: Iterable[Mod],
    overrides: dict[str, set[str]],
    inspections: dict[str, Inspection],
) -> tuple[dict[str, list[tuple[Mod, dict[str, Any]]]], list[tuple[Mod, dict[str, Any]]]]:
    categorized: dict[str, list[tuple[Mod, dict[str, Any]]]] = {group: [] for group in GROUPS}
    review: list[tuple[Mod, dict[str, Any]]] = []
    for mod in mods:
        inspection = inspections[mod.identity]
        proposal = propose(inspection)
        details = inspection_dict(inspection, proposal)
        matched = [group for group in OVERRIDE_GROUPS if mod.selectors() & overrides[group]]
        if len(matched) > 1:
            raise ImportErrorDetail(
                f"{mod.name!r} matches selectors in multiple groups: {', '.join(matched)}"
            )
        if matched:
            group = matched[0]
            details["classification_source"] = "manual-override"
            details["confidence"] = "high"
            details["proposed_group"] = group
            details["reason"] = "Matched persistent manual classification override"
            if group != "ignored":
                categorized[group].append((mod, details))
        elif proposal.confidence == "high" and proposal.group:
            details["classification_source"] = "automatic"
            categorized[proposal.group].append((mod, details))
        else:
            details["classification_source"] = "review"
            review.append((mod, details))
    return categorized, review


def write_evidence_outputs(
    output_dir: Path,
    categorized: dict[str, list[tuple[Mod, dict[str, Any]]]],
    review: list[tuple[Mod, dict[str, Any]]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {group: entries for group, entries in categorized.items()}
    payloads["review"] = review
    report_sections: list[str] = []
    for group, entries in payloads.items():
        content = []
        for mod, classification in entries:
            item = mod.as_dict()
            if group == "review":
                item["designated_category"] = None
                item["allowed_categories"] = [*GROUPS, "ignored"]
                evidence = classification.get("evidence", [])
                warnings = classification.get("warnings", [])
                lines = [
                    f"{mod.name} ({mod.filename})",
                    f"  Proposed category: {classification.get('proposed_group') or 'none'}",
                    f"  Confidence: {classification.get('confidence', 'unknown')}",
                    f"  Runtime: {classification.get('runtime', 'unknown')} "
                    f"({classification.get('runtime_confidence', 'unknown')} confidence)",
                    f"  Reason: {classification.get('reason', 'No reason recorded')}",
                ]
                if warnings:
                    lines.append("  Warnings:")
                    lines.extend(f"    - {warning}" for warning in warnings)
                if evidence:
                    lines.append("  Evidence:")
                    for detail in evidence:
                        lines.append(
                            "    - "
                            f"{detail.get('source', 'unknown')}.{detail.get('field', 'unknown')} = "
                            f"{json.dumps(detail.get('value'), ensure_ascii=False)} "
                            f"[{detail.get('strength', 'unknown')}]"
                        )
                report_sections.append("\n".join(lines))
            else:
                item["classification"] = classification
            content.append(item)
        (output_dir / f"{group}.json").write_text(
            json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    report = "\n\n".join(report_sections)
    if report:
        report += "\n"
    (output_dir / "classification-report.txt").write_text(report, encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "counts": {group: len(entries) for group, entries in payloads.items()},
        "total_included": sum(len(entries) for entries in categorized.values()),
        "total_review": len(review),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--instance",
        required=True,
        help="Prism instance folder or display name; reads its JARs and .index metadata directly",
    )
    result.add_argument(
        "--prism-root",
        type=Path,
        help="Prism data root when it is not in a standard location",
    )
    result.add_argument(
        "--overrides",
        type=Path,
        default=repo_root / "pack/classification-overrides.json",
        help="persistent classification overrides",
    )
    result.add_argument(
        "--sources",
        type=Path,
        default=repo_root / "pack/source-overrides.json",
        help="persistent download metadata for unmanaged JARs",
    )
    result.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "pack/catalog",
        help="directory for categorized JSON lists",
    )
    result.add_argument(
        "--modrinth",
        action="store_true",
        help="query Modrinth with JAR SHA-512 hashes to enrich classification evidence",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        instance = resolve_instance(args.instance, args.prism_root)
        mods, mods_dir = load_prism_instance(instance)
        mods = apply_source_overrides(mods, args.sources)
        print(f"Using Prism instance: {instance}")
        overrides = load_overrides(args.overrides)
        client = ModrinthClient() if args.modrinth else None
        inspections: dict[str, Inspection] = {}
        for mod in mods:
            inspection = inspect_mod(mod, mods_dir)
            if client:
                add_modrinth_evidence(mod, inspection, client)
            inspections[mod.identity] = inspection
        categorized, review = classify_with_evidence(mods, overrides, inspections)
        write_evidence_outputs(args.output_dir, categorized, review)
    except (ImportErrorDetail, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    included = sum(len(entries) for entries in categorized.values())
    print(f"Imported {len(mods)} unique mods: {included} classified, {len(review)} need review")
    print(f"Wrote categorized lists to {args.output_dir}")
    if review:
        review_path = args.output_dir / "review.json"
        print()
        print("Review required:")
        print(f"  1. Edit {review_path}")
        print("  2. Set each designated_category you have decided")
        print("  3. Run ./scripts/apply-review")
        print(f"  4. Rerun ./scripts/import-prism --instance {shlex.quote(args.instance)}")
        print("Do not rerun import-prism before apply-review; it regenerates review.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
