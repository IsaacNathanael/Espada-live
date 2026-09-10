import unittest
from espada.dartis import DartisRecord
from espada.dartis_split import plan_partitions


def record(name, day, scenes):
    return DartisRecord("ow", name, name + ".xml", name, day + "T00:00:00", scenes, 640, 640)


class SplitTests(unittest.TestCase):
    def test_reviewed_mosaic_and_dates_cannot_leak_transitively(self):
        rows = [record("a", "2019-01-01", "A;B"), record("b", "2019-01-02", "B;C"),
                record("c", "2019-01-03", "C"), record("d", "2019-01-03", "D"),
                record("e", "2019-02-01", "E")]
        result = plan_partitions(rows, {"a"})
        self.assertTrue(all(row["partition"] == "reviewed_development" for row in result[:4]))
        self.assertNotEqual(result[4]["partition"], "reviewed_development")
        self.assertEqual(result, plan_partitions(list(reversed(rows)), {"a"}))

    def test_mosaic_constituent_date_is_reserved(self):
        rows = [record("a", "2019-01-01", "S1A_X_20190102T120000_20190102T120025_A"),
                record("b", "2019-01-02", "different-scene")]
        self.assertTrue(all(row["partition"] == "reviewed_development"
                            for row in plan_partitions(rows, {"a"})))

    def test_invalid_review_or_date_fails(self):
        with self.assertRaises(ValueError):
            plan_partitions([record("a", "2019-01-01", "A")], {"unknown"})
        with self.assertRaises(ValueError):
            plan_partitions([record("a", "invalid", "A")], {"a"})


if __name__ == "__main__":
    unittest.main()
