#!/usr/bin/env python3
"""Persist completed review designations into classification overrides."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from .prism_list_builder import ImportErrorDetail, OVERRIDE_GROUPS
except ImportError:  # Direct execution through scripts/apply-review.
    from prism_list_builder import ImportErrorDetail, OVERRIDE_GROUPS


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportErrorDetail(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImportErrorDetail(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc


def _selector(entry: dict[str, Any], location: str) -> str:
    for field in ("project_id", "filename", "name"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ImportErrorDetail(f"{location}: no project_id, filename, or name selector")


def apply_designations(
    review: Any, overrides: Any, review_path: Path, overrides_path: Path
) -> tuple[list[dict[str, Any]], dict[str, list[str]], int]:
    if not isinstance(review, list):
        raise ImportErrorDetail(f"{review_path}: top-level value must be a list")
    if not isinstance(overrides, dict):
        raise ImportErrorDetail(f"{overrides_path}: top-level value must be an object")

    normalized_overrides: dict[str, list[str]] = {}
    owners: dict[str, str] = {}
    for group in OVERRIDE_GROUPS:
        values = overrides.get(group, [])
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ImportErrorDetail(f"{overrides_path}: {group!r} must be a list of non-empty strings")
        normalized_overrides[group] = list(values)
        for value in values:
            key = value.strip().casefold()
            previous = owners.get(key)
            if previous and previous != group:
                raise ImportErrorDetail(
                    f"{overrides_path}: selector {value!r} appears in both {previous!r} and {group!r}"
                )
            owners[key] = group

    unknown_groups = set(overrides) - set(OVERRIDE_GROUPS)
    if unknown_groups:
        raise ImportErrorDetail(
            f"{overrides_path}: unknown groups: {', '.join(sorted(unknown_groups))}"
        )

    remaining: list[dict[str, Any]] = []
    applied = 0
    for index, raw_entry in enumerate(review):
        location = f"{review_path}[{index}]"
        if not isinstance(raw_entry, dict):
            raise ImportErrorDetail(f"{location}: expected an object")
        designation = raw_entry.get("designated_category")
        if designation is None:
            remaining.append(raw_entry)
            continue
        if not isinstance(designation, str) or designation not in OVERRIDE_GROUPS:
            allowed = ", ".join(OVERRIDE_GROUPS)
            raise ImportErrorDetail(
                f"{location}: designated_category must be null or one of: {allowed}"
            )
        selector = _selector(raw_entry, location)
        key = selector.casefold()
        previous = owners.get(key)
        if previous and previous != designation:
            raise ImportErrorDetail(
                f"{location}: selector {selector!r} is already assigned to {previous!r}, not {designation!r}"
            )
        if not previous:
            normalized_overrides[designation].append(selector)
            owners[key] = designation
        applied += 1

    for group in OVERRIDE_GROUPS:
        normalized_overrides[group] = sorted(
            normalized_overrides[group], key=str.casefold
        )
    return remaining, normalized_overrides, applied


def _stage_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--review",
        type=Path,
        default=repo_root / "pack/catalog/review.json",
        help="editable generated review list",
    )
    result.add_argument(
        "--overrides",
        type=Path,
        default=repo_root / "pack/classification-overrides.json",
        help="persistent classification overrides",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        review = _load_json(args.review)
        overrides = _load_json(args.overrides)
        remaining, updated_overrides, applied = apply_designations(
            review, overrides, args.review, args.overrides
        )
        staged_overrides = _stage_json(args.overrides, updated_overrides)
        staged_review = _stage_json(args.review, remaining)
        try:
            staged_overrides.replace(args.overrides)
            staged_review.replace(args.review)
        finally:
            staged_overrides.unlink(missing_ok=True)
            staged_review.unlink(missing_ok=True)
    except (ImportErrorDetail, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Applied {applied} review designation(s); {len(remaining)} remain")
    if applied:
        print("Rerun import-prism to regenerate categorized lists from the saved overrides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
