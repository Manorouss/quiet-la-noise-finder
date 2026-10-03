"""Validate the complete, synthetic input inventory for the Java H/F harness."""
from __future__ import annotations

import csv
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def rows(name: str) -> list[list[str]]:
    with (ROOT / name).open(newline="") as source:
        return list(csv.reader(source, delimiter="\t"))


class RegressionVectorInventoryTests(unittest.TestCase):
    def test_h_branch_vector_inventory_is_complete_and_well_formed(self):
        data = rows("h_regression_inputs.tsv")
        self.assertEqual(len(data), 259)
        self.assertTrue(all(len(row) == 14 and row[1] == "H" for row in data))
        self.assertEqual(sum(row[0].startswith("zero-") for row in data), 43)
        self.assertEqual(sum(row[0].startswith("positive-") for row in data), 129)
        self.assertEqual(sum(row[0].startswith("limit-") for row in data), 72)

    def test_f_branch_vector_inventory_covers_domain_and_limits(self):
        data = rows("f_regression_inputs.tsv")
        self.assertEqual(len(data), 1073)
        self.assertTrue(all(len(row) == 14 and row[1] == "F" for row in data))
        zero = [row for row in data if row[0].startswith("fzero-")]
        self.assertEqual(len(zero), 288)
        self.assertEqual(sum(float(row[5]) == 0 and (float(row[8]) > 0 or float(row[9]) > 0) for row in zero), 216)
        self.assertEqual(sum(float(row[5]) == 0 and float(row[8]) == 0 and float(row[9]) == 0 for row in zero), 72)
        self.assertEqual(sum(row[0].startswith("positive-") for row in data), 768)
        self.assertEqual(sum(row[0].startswith("invalid-") for row in data), 17)


if __name__ == "__main__":
    unittest.main()
