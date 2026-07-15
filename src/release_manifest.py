#!/usr/bin/env python3
"""Create the machine-readable release contract and checksum file."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROFILES = ("core", "client", "server")


class ReleaseError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{path}: expected a JSON object")
    return value


def required_string(value: dict[str, Any], key: str, path: Path) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ReleaseError(f"{path}: {key!r} must be a non-empty string")
    return result.strip()


def digest(path: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                sha256.update(block)
                size += len(block)
    except FileNotFoundError as exc:
        raise ReleaseError(f"release asset does not exist: {path}") from exc
    return sha256.hexdigest(), size


def build_metadata(
    pack_path: Path,
    catalog_manifest_path: Path,
    dist: Path,
    repository: str,
    tag: str,
    source_commit: str,
) -> dict[str, Any]:
    pack = load_object(pack_path)
    catalog = load_object(catalog_manifest_path)
    version = required_string(pack, "version", pack_path)
    expected_tag = f"pack-v{version}"
    if tag != expected_tag:
        raise ReleaseError(
            f"release tag {tag!r} does not match pack version; expected {expected_tag!r}"
        )
    if catalog.get("total_review") != 0:
        raise ReleaseError(
            f"classification review must be empty before release; found {catalog.get('total_review')!r}"
        )
    if not source_commit or any(character not in "0123456789abcdef" for character in source_commit.casefold()):
        raise ReleaseError("source commit must be a hexadecimal Git commit ID")

    packs: dict[str, Any] = {}
    checksums: list[tuple[str, str]] = []
    for profile in PROFILES:
        asset = f"smp-{version}-{profile}.mrpack"
        sha256, size = digest(dist / asset)
        checksums.append((sha256, asset))
        packs[profile] = {
            "asset": asset,
            "format": "mrpack",
            "sha256": sha256,
            "size": size,
        }

    manifest = {
        "schema_version": 1,
        "release": version,
        "tag": tag,
        "repository": repository,
        "source_commit": source_commit,
        "minecraft_version": required_string(pack, "minecraft", pack_path),
        "loader": {
            "type": required_string(pack, "loader", pack_path),
            "version": required_string(pack, "loader_version", pack_path),
        },
        "packs": packs,
    }
    (dist / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (dist / "SHA256SUMS").write_text(
        "".join(f"{sha256}  {asset}\n" for sha256, asset in checksums), encoding="utf-8"
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dist", type=Path, default=root / "dist")
    result.add_argument("--pack", type=Path, default=root / "pack/pack.json")
    result.add_argument(
        "--catalog-manifest",
        type=Path,
        default=root / "pack/catalog/manifest.json",
    )
    result.add_argument("--repository", default="yurei-dll/smp")
    result.add_argument("--tag", required=True)
    result.add_argument("--source-commit", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        build_metadata(
            args.pack,
            args.catalog_manifest,
            args.dist,
            args.repository,
            args.tag,
            args.source_commit,
        )
    except (ReleaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Built {args.dist / 'release-manifest.json'} and {args.dist / 'SHA256SUMS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
