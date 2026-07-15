#!/usr/bin/env python3
"""Build deterministic Modrinth .mrpack archives from classified catalogs."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


GROUPS = {"core", "client-optional", "server-required", "server-curated"}
LOADERS = {"forge", "neoforge", "fabric-loader", "quilt-loader"}


class BuildError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BuildError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"{path}: expected a JSON object")
    return value


def required_string(value: dict[str, Any], key: str, path: Path) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise BuildError(f"{path}: {key!r} must be a non-empty string")
    return result.strip()


def load_profile(path: Path) -> tuple[str, list[str]]:
    profile = load_object(path)
    name = required_string(profile, "name", path)
    include = profile.get("include")
    if not isinstance(include, list) or not include or not all(isinstance(item, str) for item in include):
        raise BuildError(f"{path}: 'include' must be a non-empty string list")
    unknown = set(include) - GROUPS
    if unknown:
        raise BuildError(f"{path}: unknown catalog groups: {', '.join(sorted(unknown))}")
    if len(include) != len(set(include)):
        raise BuildError(f"{path}: duplicate catalog group")
    return name, include


def file_environment(group: str) -> dict[str, str]:
    if group == "client-optional":
        return {"client": "required", "server": "unsupported"}
    if group in {"server-required", "server-curated"}:
        return {"client": "unsupported", "server": "required"}
    return {"client": "required", "server": "required"}


def catalog_files(catalog_dir: Path, groups: list[str]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for group in groups:
        path = catalog_dir / f"{group}.json"
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BuildError(f"catalog does not exist: {path}") from exc
        except json.JSONDecodeError as exc:
            raise BuildError(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc
        if not isinstance(entries, list):
            raise BuildError(f"{path}: expected a JSON list")
        for index, entry in enumerate(entries):
            location = f"{path}[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{location}: expected an object")
                continue
            filename = entry.get("filename")
            classification = entry.get("classification")
            download_url = entry.get("download_url")
            if not isinstance(filename, str) or not filename or PurePosixPath(filename).name != filename:
                errors.append(f"{location}: unsafe or missing filename")
                continue
            key = filename.casefold()
            if key in seen:
                errors.append(f"{location}: duplicate output filename {filename!r}")
                continue
            seen.add(key)
            if not isinstance(download_url, str) or not download_url.startswith("https://"):
                errors.append(f"{location}: {filename} has no HTTPS download_url")
                continue
            if not isinstance(classification, dict):
                errors.append(f"{location}: missing classification evidence")
                continue
            sha1 = classification.get("sha1")
            sha512 = classification.get("sha512")
            size = classification.get("file_size")
            if not isinstance(sha1, str) or len(sha1) != 40:
                errors.append(f"{location}: missing valid SHA-1")
                continue
            if not isinstance(sha512, str) or len(sha512) != 128:
                errors.append(f"{location}: missing valid SHA-512")
                continue
            if not isinstance(size, int) or size < 0:
                errors.append(f"{location}: missing valid file size")
                continue
            files.append(
                {
                    "path": f"mods/{filename}",
                    "hashes": {"sha1": sha1, "sha512": sha512},
                    "env": file_environment(group),
                    "downloads": [download_url],
                    "fileSize": size,
                }
            )
    if errors:
        raise BuildError("cannot build pack:\n- " + "\n- ".join(errors))
    return sorted(files, key=lambda item: item["path"].casefold())


def build(profile_path: Path, pack_path: Path, catalog_dir: Path, output: Path) -> None:
    pack = load_object(pack_path)
    profile_name, groups = load_profile(profile_path)
    loader = required_string(pack, "loader", pack_path)
    if loader not in LOADERS:
        raise BuildError(f"{pack_path}: unsupported loader {loader!r}")
    version = required_string(pack, "version", pack_path)
    manifest: dict[str, Any] = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": version,
        "name": profile_name,
        "summary": required_string(pack, "summary", pack_path),
        "files": catalog_files(catalog_dir, groups),
        "dependencies": {
            "minecraft": required_string(pack, "minecraft", pack_path),
            loader: required_string(pack, "loader_version", pack_path),
        },
    }
    encoded = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    info = zipfile.ZipInfo("modrinth.index.json", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, encoded)


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--pack", type=Path, default=root / "pack/pack.json")
    result.add_argument("--catalog-dir", type=Path, default=root / "pack/catalog")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        build(args.profile, args.pack, args.catalog_dir, args.output)
    except (BuildError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Built {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
