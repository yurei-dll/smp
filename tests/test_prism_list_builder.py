import json
import tempfile
import unittest
from pathlib import Path

from src.prism_list_builder import (
    ImportErrorDetail,
    Mod,
    apply_source_overrides,
    load_overrides,
    load_prism_instance,
    resolve_instance,
    write_evidence_outputs,
)


class PrismListBuilderTests(unittest.TestCase):
    def test_rejects_selector_in_multiple_override_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.json"
            path.write_text(
                json.dumps({"core": ["same"], "client": ["SAME"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ImportErrorDetail, "appears in both"):
                load_overrides(path)

    def test_loads_named_instance_index_and_unmanaged_jars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "PrismLauncher"
            instance = root / "instances/folder-name"
            mods = instance / "minecraft/mods"
            index = mods / ".index"
            index.mkdir(parents=True)
            (instance / "instance.cfg").write_text(
                "[General]\nname=Friendly Name\n", encoding="utf-8"
            )
            (mods / "managed.jar").write_bytes(b"managed")
            (mods / "unmanaged.jar").write_bytes(b"unmanaged")
            (index / "managed.pw.toml").write_text(
                """
filename = "managed.jar"
name = "Managed Mod"
side = "client"
[download]
url = "https://cdn.modrinth.com/data/project/version/managed.jar"
[update.modrinth]
mod-id = "project"
version = "version"
""",
                encoding="utf-8",
            )

            resolved = resolve_instance("Friendly Name", root)
            imported, mods_dir = load_prism_instance(resolved)

            self.assertEqual(resolved, instance)
            self.assertEqual(mods_dir, mods)
            self.assertEqual([mod.filename for mod in imported], ["managed.jar", "unmanaged.jar"])
            managed = imported[0]
            self.assertEqual(managed.project_id, "project")
            self.assertEqual(managed.platform_version_id, "version")
            self.assertEqual(managed.declared_side, "client")

    def test_applies_download_source_to_unmanaged_mod(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "unmanaged.jar": {
                            "source": "modrinth",
                            "project_id": "project",
                            "platform_version_id": "version",
                            "download_url": "https://cdn.modrinth.com/data/project/version/unmanaged.jar",
                        }
                    }
                ),
                encoding="utf-8",
            )
            mods = [Mod("unmanaged.jar", "Unmanaged", "", "unknown", None, None)]
            updated = apply_source_overrides(mods, path)
            self.assertEqual(updated[0].project_id, "project")
            self.assertTrue(updated[0].download_url.startswith("https://cdn.modrinth.com/"))

    def test_review_json_is_editable_and_reasoning_goes_to_text_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            mod = Mod("example.jar", "Example", "", "unknown", None, None)
            classification = {
                "runtime": "both",
                "runtime_confidence": "high",
                "proposed_group": "core",
                "confidence": "medium",
                "reason": "Needs a human decision",
                "warnings": ["Conflicting metadata"],
                "evidence": [
                    {"source": "test", "field": "side", "value": "both", "strength": "high"}
                ],
            }
            write_evidence_outputs(
                output,
                {"core": [], "client": [], "server": []},
                [(mod, classification)],
            )
            review = json.loads((output / "review.json").read_text(encoding="utf-8"))
            self.assertNotIn("classification", review[0])
            self.assertIsNone(review[0]["designated_category"])
            report = (output / "classification-report.txt").read_text(encoding="utf-8")
            self.assertIn("Needs a human decision", report)
            self.assertIn("test.side = \"both\" [high]", report)


if __name__ == "__main__":
    unittest.main()
