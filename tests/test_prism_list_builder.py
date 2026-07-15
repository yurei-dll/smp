import json
import tempfile
import unittest
from pathlib import Path

from src.prism_list_builder import (
    ImportErrorDetail,
    load_overrides,
    load_prism_instance,
    resolve_instance,
)


class PrismListBuilderTests(unittest.TestCase):
    def test_rejects_selector_in_multiple_override_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.json"
            path.write_text(
                json.dumps({"core": ["same"], "client-optional": ["SAME"]}),
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


if __name__ == "__main__":
    unittest.main()
