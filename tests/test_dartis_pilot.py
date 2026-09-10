import unittest
from copy import deepcopy

from espada.dartis_pilot import duplicate_audit, select_pilot_rows


def plan():
    rows = []
    for partition in ("train", "validation", "reserved_test"):
        for subset in ("ow", "oc", "nw", "nc"):
            for index in range(4):
                rows.append({"partition": partition, "subset": subset,
                             "jpg_file": f"{partition}-{subset}-{index}.jpg",
                             "group_id": f"{partition}-{subset}-g{index // 2}"})
    return {"report": {"status": "PLAN_READY"}, "rows": rows}


class PilotTests(unittest.TestCase):
    def test_selection_is_balanced_deterministic_and_never_reads_test(self):
        selected = select_pilot_rows(plan(), train_per_subset=3, validation_per_subset=2)
        self.assertEqual(selected, select_pilot_rows(deepcopy(plan()), train_per_subset=3,
                                                     validation_per_subset=2))
        self.assertEqual(len(selected), 20)
        self.assertNotIn("reserved_test", {row["partition"] for row in selected})

    def test_cross_partition_group_is_rejected(self):
        fixture = plan()
        fixture["rows"][16]["group_id"] = fixture["rows"][0]["group_id"]
        with self.assertRaises(ValueError):
            select_pilot_rows(fixture, train_per_subset=4, validation_per_subset=4)

    def test_duplicate_audit_separates_exact_failure_from_review_flag(self):
        rows = [{"partition": "train", "jpg_file": "a", "image_sha256": "one", "difference_hash": "same"},
                {"partition": "validation", "jpg_file": "b", "image_sha256": "two", "difference_hash": "same"}]
        self.assertEqual(duplicate_audit(rows)["status"], "REVIEW_REQUIRED")
        rows[1]["image_sha256"] = "one"
        self.assertEqual(duplicate_audit(rows)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
