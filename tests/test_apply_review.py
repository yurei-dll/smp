import unittest
from pathlib import Path

from src.apply_review import apply_designations
from src.prism_list_builder import ImportErrorDetail


class ApplyReviewTests(unittest.TestCase):
    def test_applies_designated_entries_and_keeps_null_entries(self):
        review = [
            {"name": "Applied", "project_id": "project-a", "designated_profile": "core"},
            {"name": "Waiting", "project_id": "project-b", "designated_profile": None},
        ]
        overrides = {
            "core": [],
            "client-optional": [],
            "server-required": [],
            "server-curated": [],
            "ignored": [],
        }
        remaining, updated, applied = apply_designations(
            review, overrides, Path("review.json"), Path("overrides.json")
        )
        self.assertEqual(applied, 1)
        self.assertEqual([entry["name"] for entry in remaining], ["Waiting"])
        self.assertEqual(updated["core"], ["project-a"])

    def test_existing_assignment_is_idempotent(self):
        review = [{"project_id": "same", "designated_profile": "core"}]
        overrides = {
            "core": ["same"],
            "client-optional": [],
            "server-required": [],
            "server-curated": [],
            "ignored": [],
        }
        remaining, updated, applied = apply_designations(
            review, overrides, Path("review.json"), Path("overrides.json")
        )
        self.assertEqual((remaining, applied), ([], 1))
        self.assertEqual(updated["core"], ["same"])

    def test_rejects_invalid_profile_without_applying_anything(self):
        review = [{"project_id": "project", "designated_profile": "banana"}]
        overrides = {group: [] for group in (
            "core", "client-optional", "server-required", "server-curated", "ignored"
        )}
        with self.assertRaisesRegex(ImportErrorDetail, "must be null or one of"):
            apply_designations(review, overrides, Path("review.json"), Path("overrides.json"))

    def test_rejects_reassignment_to_different_group(self):
        review = [{"project_id": "same", "designated_profile": "client-optional"}]
        overrides = {
            "core": ["same"],
            "client-optional": [],
            "server-required": [],
            "server-curated": [],
            "ignored": [],
        }
        with self.assertRaisesRegex(ImportErrorDetail, "already assigned"):
            apply_designations(review, overrides, Path("review.json"), Path("overrides.json"))


if __name__ == "__main__":
    unittest.main()
