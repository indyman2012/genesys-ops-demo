from __future__ import annotations

import unittest
from io import BytesIO

from openpyxl import Workbook

from tools.queues.corporate_training_roster_lib import (
    is_corporate_training_queue_name,
    parse_names_from_workbook,
)


class CorporateTrainingRosterTests(unittest.TestCase):
    def test_is_corporate_training_queue_name(self):
        self.assertTrue(is_corporate_training_queue_name("Sales - NVL - Corporate Training-2"))
        self.assertTrue(is_corporate_training_queue_name("Patient Services - NVL Corporate Training"))
        self.assertFalse(is_corporate_training_queue_name("Sales - NVL - Henderson"))

    def test_parse_names_from_workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Agent Name"])
        ws.append(["Jane Doe"])
        ws.append(["John Smith and Mary Jones"])
        buf = BytesIO()
        wb.save(buf)
        names = parse_names_from_workbook(buf.getvalue())
        self.assertEqual(names, ["Jane Doe", "John Smith", "Mary Jones"])


if __name__ == "__main__":
    unittest.main()
