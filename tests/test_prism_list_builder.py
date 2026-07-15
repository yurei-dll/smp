import json
import tempfile
import unittest
from pathlib import Path

from src.prism_list_builder import (
    ImportErrorDetail,
    classify,
    load_overrides,
    load_prism_lists,
    platform_identity,
    write_outputs,
)


class PrismListBuilderTests(unittest.TestCase):
    def test_extracts_modrinth_project_identity(self):
        self.assertEqual(
            platform_identity("https://modrinth.com/mod/DSVgwcji"),
            ("modrinth", "DSVgwcji"),
        )

    def test_imports_deduplicates_classifies_and_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = [
                {
                    "filename": "AI-Improvements-1.21-0.5.3.jar",
                    "name": "AI-Improvements",
                    "url": "https://modrinth.com/mod/DSVgwcji",
                    "version": "0.5.3",
                },
                {
                    "filename": "accessories.jar",
                    "name": "Accessories",
                    "url": "https://modrinth.com/mod/jtmvUHXj",
                    "version": "1.1.0",
                },
            ]
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(json.dumps(exported), encoding="utf-8")
            second.write_text(json.dumps(exported[:1]), encoding="utf-8")
            overrides_path = root / "overrides.json"
            overrides_path.write_text(
                json.dumps({"server-curated": ["DSVgwcji"]}), encoding="utf-8"
            )

            mods = load_prism_lists([first, second])
            categorized, review = classify(mods, load_overrides(overrides_path))
            write_outputs(root / "out", categorized, review)

            self.assertEqual(len(mods), 2)
            self.assertEqual([mod.name for mod in categorized["server-curated"]], ["AI-Improvements"])
            self.assertEqual([mod.name for mod in review], ["Accessories"])
            manifest = json.loads((root / "out/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["server-curated"], 1)
            self.assertEqual(manifest["total_review"], 1)

    def test_rejects_conflicting_duplicate_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "mods.json"
            path.write_text(
                json.dumps(
                    [
                        {"filename": "a.jar", "name": "A", "url": "https://modrinth.com/mod/id", "version": "1"},
                        {"filename": "b.jar", "name": "A", "url": "https://modrinth.com/mod/id", "version": "2"},
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ImportErrorDetail, "conflicting entries"):
                load_prism_lists([path])

    def test_rejects_selector_in_multiple_override_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.json"
            path.write_text(
                json.dumps({"core": ["same"], "client-optional": ["SAME"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ImportErrorDetail, "appears in both"):
                load_overrides(path)


if __name__ == "__main__":
    unittest.main()
