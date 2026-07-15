import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from src.mod_classifier import (
    Evidence,
    Inspection,
    add_modrinth_evidence,
    inspect_jar,
    propose,
)


class FakeModrinthClient:
    def version_by_sha512(self, digest):
        return {"id": "version-1", "project_id": "project-1"}

    def project(self, project_id):
        return {"client_side": "optional", "server_side": "unsupported"}


class ModClassifierTests(unittest.TestCase):
    def make_jar(self, files):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "example.jar"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        self.addCleanup(temporary.cleanup)
        return path

    def test_fabric_client_environment_is_high_confidence(self):
        path = self.make_jar(
            {
                "fabric.mod.json": json.dumps(
                    {"schemaVersion": 1, "id": "example", "version": "1.0", "environment": "client"}
                )
            }
        )
        inspection = inspect_jar(path)
        self.assertEqual(inspection.runtime, "client")
        self.assertEqual(inspection.runtime_confidence, "high")
        self.assertIsNotNone(inspection.sha512)

    def test_neoforge_client_side_only_is_high_confidence(self):
        path = self.make_jar(
            {
                "META-INF/neoforge.mods.toml": """
modLoader="javafml"
loaderVersion="[1,)"
license="MIT"
clientSideOnly=true
[[mods]]
modId="example"
version="1.0"
displayTest="IGNORE_ALL_VERSION"
"""
            }
        )
        inspection = inspect_jar(path)
        self.assertEqual(inspection.runtime, "client")
        self.assertEqual(inspection.runtime_confidence, "high")
        fields = {item.field for item in inspection.evidence}
        self.assertIn("displayTest", fields)

    def test_hash_resolved_platform_and_loader_evidence_auto_classifies(self):
        inspection = Inspection(
            filename="example.jar",
            sha512="abc",
            runtime="client",
            runtime_confidence="high",
            evidence=[Evidence("fabric.mod.json", "environment", "client", "high")],
        )
        mod = SimpleNamespace(source="modrinth", project_id="project-1")
        add_modrinth_evidence(mod, inspection, FakeModrinthClient())
        proposal = propose(inspection)
        self.assertEqual(proposal.group, "client")
        self.assertEqual(proposal.confidence, "high")

    def test_runtime_without_pack_policy_requires_review(self):
        inspection = Inspection(
            filename="example.jar",
            runtime="client",
            runtime_confidence="high",
        )
        proposal = propose(inspection)
        self.assertIsNone(proposal.group)
        self.assertEqual(proposal.confidence, "medium")

    def test_platform_only_proposal_remains_medium(self):
        inspection = Inspection(
            filename="example.jar",
            platform={"client_side": "required", "server_side": "unsupported"},
        )
        proposal = propose(inspection)
        self.assertEqual(proposal.group, "client")
        self.assertEqual(proposal.confidence, "medium")

    def test_prism_side_policy_auto_classifies(self):
        expected = {
            "both": "core",
            "client": "client",
            "server": "server",
        }
        for side, group in expected.items():
            with self.subTest(side=side):
                inspection = Inspection(
                    filename="example.jar",
                    runtime=side,
                    runtime_confidence="high",
                    prism_side=side,
                )
                proposal = propose(inspection)
                self.assertEqual(proposal.group, group)
                self.assertEqual(proposal.confidence, "high")


if __name__ == "__main__":
    unittest.main()
