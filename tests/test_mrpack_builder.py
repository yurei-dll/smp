import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.mrpack_builder import BuildError, build


class MrpackBuilderTests(unittest.TestCase):
    def fixture(self, root, *, download_url="https://cdn.modrinth.com/data/a/v/a.jar"):
        catalog = root / "catalog"
        catalog.mkdir()
        entry = {
            "filename": "a.jar",
            "download_url": download_url,
            "classification": {
                "sha1": "a" * 40,
                "sha512": "b" * 128,
                "file_size": 123,
            },
        }
        (catalog / "core.json").write_text(json.dumps([entry]), encoding="utf-8")
        profile = root / "profile.json"
        profile.write_text(json.dumps({"name": "Test Client", "include": ["core"]}), encoding="utf-8")
        pack = root / "pack.json"
        pack.write_text(
            json.dumps(
                {
                    "name": "Test",
                    "version": "1.2.3",
                    "summary": "Test pack",
                    "minecraft": "1.21.1",
                    "loader": "neoforge",
                    "loader_version": "21.1.214",
                }
            ),
            encoding="utf-8",
        )
        return catalog, profile, pack

    def test_builds_deterministic_spec_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog, profile, pack = self.fixture(root)
            first = root / "first.mrpack"
            second = root / "second.mrpack"
            build(profile, pack, catalog, first)
            build(profile, pack, catalog, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), ["modrinth.index.json"])
                manifest = json.loads(archive.read("modrinth.index.json"))
            self.assertEqual(manifest["formatVersion"], 1)
            self.assertEqual(manifest["dependencies"]["minecraft"], "1.21.1")
            self.assertEqual(manifest["dependencies"]["neoforge"], "21.1.214")
            self.assertEqual(manifest["files"][0]["hashes"]["sha1"], "a" * 40)

    def test_rejects_missing_download_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog, profile, pack = self.fixture(root, download_url="")
            with self.assertRaisesRegex(BuildError, "no HTTPS download_url"):
                build(profile, pack, catalog, root / "out.mrpack")


if __name__ == "__main__":
    unittest.main()
