import json
import tempfile
import unittest
from pathlib import Path

from src.release_manifest import PROFILES, ReleaseError, build_metadata


class ReleaseManifestTests(unittest.TestCase):
    def fixture(self, root, review=0):
        pack = root / "pack.json"
        pack.write_text(
            json.dumps(
                {
                    "version": "1.2.3",
                    "minecraft": "1.21.1",
                    "loader": "neoforge",
                    "loader_version": "21.1.214",
                }
            ),
            encoding="utf-8",
        )
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps({"total_review": review}), encoding="utf-8")
        dist = root / "dist"
        dist.mkdir()
        for profile in PROFILES:
            (dist / f"smp-1.2.3-{profile}.mrpack").write_bytes(profile.encode())
        return pack, catalog, dist

    def test_builds_consumer_manifest_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, catalog, dist = self.fixture(root)
            manifest = build_metadata(
                pack, catalog, dist, "owner/repo", "pack-v1.2.3", "a" * 40
            )
            self.assertEqual(manifest["release"], "1.2.3")
            self.assertEqual(manifest["repository"], "owner/repo")
            self.assertEqual(set(manifest["packs"]), set(PROFILES))
            self.assertEqual(manifest["packs"]["client"]["format"], "mrpack")
            self.assertEqual(len(manifest["packs"]["client"]["sha256"]), 64)
            self.assertEqual(len((dist / "SHA256SUMS").read_text().splitlines()), 3)

    def test_rejects_tag_version_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, catalog, dist = self.fixture(root)
            with self.assertRaisesRegex(ReleaseError, "does not match pack version"):
                build_metadata(pack, catalog, dist, "owner/repo", "pack-v2.0.0", "a" * 40)

    def test_rejects_nonempty_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, catalog, dist = self.fixture(root, review=2)
            with self.assertRaisesRegex(ReleaseError, "review must be empty"):
                build_metadata(pack, catalog, dist, "owner/repo", "pack-v1.2.3", "a" * 40)


if __name__ == "__main__":
    unittest.main()
