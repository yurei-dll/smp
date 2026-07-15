"""Evidence collection and confidence-based mod classification."""

from __future__ import annotations

import hashlib
import json
import tomllib
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Evidence:
    source: str
    field: str
    value: Any
    strength: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "field": self.field,
            "value": self.value,
            "strength": self.strength,
        }


@dataclass
class Inspection:
    filename: str
    sha512: str | None = None
    runtime: str = "unknown"
    runtime_confidence: str = "unknown"
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    platform: dict[str, Any] | None = None
    prism_side: str | None = None


@dataclass(frozen=True)
class Proposal:
    group: str | None
    confidence: str
    reason: str


class ModrinthClient:
    """Small, dependency-free client for exact-file and project metadata."""

    API = "https://api.modrinth.com/v2"

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self._projects: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, dict[str, Any] | None] = {}

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.API}{path}",
            headers={"User-Agent": "yurei-dll/smp-classifier (GitHub Actions)"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value

    def version_by_sha512(self, digest: str) -> dict[str, Any] | None:
        if digest not in self._versions:
            try:
                self._versions[digest] = self._get(
                    f"/version_file/{digest}?algorithm=sha512"
                )
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self._versions[digest] = None
                else:
                    raise
        return self._versions[digest]

    def project(self, project_id: str) -> dict[str, Any]:
        if project_id not in self._projects:
            self._projects[project_id] = self._get(f"/project/{project_id}")
        return self._projects[project_id]


def _read_json(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    value = json.loads(archive.read(name).decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("top-level value is not an object")
    return value


def _read_toml(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    return tomllib.loads(archive.read(name).decode("utf-8-sig"))


def _record_fabric(inspection: Inspection, metadata: dict[str, Any]) -> None:
    environment = metadata.get("environment", "*")
    inspection.evidence.append(Evidence("fabric.mod.json", "environment", environment, "high"))
    if environment == "client":
        inspection.runtime = "client"
        inspection.runtime_confidence = "high"
    elif environment == "server":
        inspection.runtime = "server"
        inspection.runtime_confidence = "high"
    elif environment == "*":
        inspection.runtime = "both"
        inspection.runtime_confidence = "high"
    else:
        inspection.warnings.append(f"unrecognized Fabric environment: {environment!r}")

    inspection.evidence.append(Evidence("fabric.mod.json", "id", metadata.get("id"), "high"))
    inspection.evidence.append(Evidence("fabric.mod.json", "version", metadata.get("version"), "high"))
    entrypoints = metadata.get("entrypoints")
    if isinstance(entrypoints, dict):
        present = sorted(key for key in ("main", "client", "server") if entrypoints.get(key))
        if present:
            inspection.evidence.append(Evidence("fabric.mod.json", "entrypoints", present, "medium"))


def _record_forge(inspection: Inspection, metadata: dict[str, Any], source: str) -> None:
    client_only = metadata.get("clientSideOnly")
    if isinstance(client_only, bool):
        inspection.evidence.append(Evidence(source, "clientSideOnly", client_only, "high" if client_only else "medium"))
        if client_only:
            inspection.runtime = "client"
            inspection.runtime_confidence = "high"

    mods = metadata.get("mods")
    if isinstance(mods, list):
        ids = [item.get("modId") for item in mods if isinstance(item, dict) and item.get("modId")]
        if ids:
            inspection.evidence.append(Evidence(source, "modIds", ids, "high"))
        display_tests = [
            item.get("displayTest")
            for item in mods
            if isinstance(item, dict) and item.get("displayTest") is not None
        ]
        if display_tests:
            inspection.evidence.append(Evidence(source, "displayTest", display_tests, "medium"))

    dependencies = metadata.get("dependencies")
    if isinstance(dependencies, dict):
        sides: dict[str, list[Any]] = {}
        for mod_id, entries in dependencies.items():
            if isinstance(entries, list):
                sides[str(mod_id)] = [
                    entry.get("side", "BOTH") for entry in entries if isinstance(entry, dict)
                ]
        if sides:
            inspection.evidence.append(Evidence(source, "dependencySides", sides, "medium"))


def inspect_jar(path: Path) -> Inspection:
    inspection = Inspection(filename=path.name)
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    inspection.sha512 = digest.hexdigest()
    inspection.evidence.append(Evidence("jar", "sha512", inspection.sha512, "high"))

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "fabric.mod.json" in names:
                try:
                    _record_fabric(inspection, _read_json(archive, "fabric.mod.json"))
                except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    inspection.warnings.append(f"cannot parse fabric.mod.json: {exc}")

            forge_names = (
                "META-INF/neoforge.mods.toml",
                "META-INF/mods.toml",
            )
            for name in forge_names:
                if name in names:
                    try:
                        _record_forge(inspection, _read_toml(archive, name), name)
                    except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
                        inspection.warnings.append(f"cannot parse {name}: {exc}")
    except zipfile.BadZipFile as exc:
        inspection.warnings.append(f"not a readable JAR/ZIP: {exc}")
    return inspection


def inspect_mod(mod: Any, mods_dir: Path | None) -> Inspection:
    if mods_dir is None:
        result = Inspection(filename=mod.filename)
        result.warnings.append("JAR inspection unavailable")
        return result
    path = mods_dir / mod.filename
    if not path.is_file():
        result = Inspection(filename=mod.filename)
        result.warnings.append(f"JAR not found: {path}")
        return result
    result = inspect_jar(path)
    declared_side = getattr(mod, "declared_side", None)
    if declared_side in {"client", "server", "both"}:
        result.prism_side = declared_side
        result.evidence.append(Evidence("prism-packwiz-index", "side", declared_side, "high"))
        if result.runtime_confidence != "high":
            result.runtime = declared_side
            result.runtime_confidence = "high"
        elif result.runtime != declared_side:
            result.warnings.append(
                f"Prism index side {declared_side!r} conflicts with JAR runtime {result.runtime!r}"
            )
            result.runtime_confidence = "medium"
    return result


def add_modrinth_evidence(mod: Any, inspection: Inspection, client: ModrinthClient) -> None:
    if mod.source != "modrinth" or not mod.project_id:
        return
    if not inspection.sha512:
        inspection.warnings.append("cannot resolve exact Modrinth file without a JAR hash")
        return
    try:
        version = client.version_by_sha512(inspection.sha512)
        if version is None:
            inspection.warnings.append("JAR SHA-512 was not found on Modrinth")
            return
        resolved_project = version.get("project_id")
        if not isinstance(resolved_project, str):
            inspection.warnings.append("Modrinth version response has no project_id")
            return
        if resolved_project.casefold() != mod.project_id.casefold():
            inspection.warnings.append(
                f"Prism project {mod.project_id!r} conflicts with hash-resolved project {resolved_project!r}"
            )
            return
        project = client.project(resolved_project)
        inspection.platform = {
            "source": "modrinth",
            "project_id": resolved_project,
            "version_id": version.get("id"),
            "client_side": project.get("client_side"),
            "server_side": project.get("server_side"),
        }
        inspection.evidence.extend(
            [
                Evidence("modrinth-exact-file", "version_id", version.get("id"), "high"),
                Evidence("modrinth-project", "client_side", project.get("client_side"), "medium"),
                Evidence("modrinth-project", "server_side", project.get("server_side"), "medium"),
            ]
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        inspection.warnings.append(f"Modrinth lookup failed: {exc}")


def propose(inspection: Inspection) -> Proposal:
    """Propose a final group, applying only documented confidence rules."""
    if inspection.prism_side and inspection.runtime_confidence == "high":
        prism_groups = {
            "both": "core",
            "client": "client-optional",
            "server": "server-curated",
        }
        return Proposal(
            prism_groups[inspection.prism_side],
            "high",
            f"Prism side {inspection.prism_side!r} mapped by repository pack policy",
        )

    platform = inspection.platform
    if not platform:
        reason = (
            "Runtime environment was identified, but required-versus-optional pack policy is unknown"
            if inspection.runtime_confidence == "high"
            else "No authoritative runtime and pack-policy combination was found"
        )
        return Proposal(None, "medium" if inspection.runtime_confidence == "high" else "unknown", reason)

    client_side = platform.get("client_side")
    server_side = platform.get("server_side")
    runtime = inspection.runtime

    group: str | None = None
    if runtime == "client" and client_side in {"required", "optional"}:
        group = "client-optional"
    elif runtime == "server" and server_side == "required":
        group = "server-required"
    elif runtime == "server" and server_side == "optional":
        group = "server-curated"
    elif runtime == "both" and client_side == "required" and server_side == "required":
        group = "core"

    if group and inspection.runtime_confidence == "high":
        return Proposal(group, "high", "Loader runtime metadata agrees with hash-resolved platform metadata")

    proposed: str | None = None
    if client_side in {"required", "optional"} and server_side == "unsupported":
        proposed = "client-optional"
    elif server_side in {"required", "optional"} and client_side == "unsupported":
        proposed = "server-required" if server_side == "required" else "server-curated"
    elif client_side == "required" and server_side == "required":
        proposed = "core"
    return Proposal(proposed, "medium", "Platform policy lacks authoritative matching runtime metadata")


def inspection_dict(inspection: Inspection, proposal: Proposal) -> dict[str, Any]:
    return {
        "runtime": inspection.runtime,
        "runtime_confidence": inspection.runtime_confidence,
        "proposed_group": proposal.group,
        "confidence": proposal.confidence,
        "reason": proposal.reason,
        "sha512": inspection.sha512,
        "platform": inspection.platform,
        "evidence": [item.as_dict() for item in inspection.evidence],
        "warnings": inspection.warnings,
    }
