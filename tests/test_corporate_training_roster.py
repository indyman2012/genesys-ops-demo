from __future__ import annotations

import unittest
from io import BytesIO

from openpyxl import Workbook

from tools.queues.corporate_training_roster_lib import (
    is_corporate_training_queue_name,
    normalize_roster_email,
    parse_emails_from_workbook,
)


class CorporateTrainingRosterTests(unittest.TestCase):
    def test_is_corporate_training_queue_name(self):
        self.assertTrue(is_corporate_training_queue_name("Sales - NVL - Corporate Training-2"))
        self.assertTrue(is_corporate_training_queue_name("Patient Services - NVL Corporate Training"))
        self.assertFalse(is_corporate_training_queue_name("Sales - NVL - Henderson"))

    def test_normalize_roster_email(self):
        self.assertEqual(normalize_roster_email("  User@Example.COM "), "user@example.com")

    def test_parse_emails_from_workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Work Email"])
        ws.append(["agent.one@archwellhealth.com"])
        ws.append(["agent.two@archwellhealth.com, agent.three@archwellhealth.com"])
        buf = BytesIO()
        wb.save(buf)
        emails = parse_emails_from_workbook(buf.getvalue())
        self.assertEqual(
            emails,
            [
                "agent.one@archwellhealth.com",
                "agent.two@archwellhealth.com",
                "agent.three@archwellhealth.com",
            ],
        )


if __name__ == "__main__":
    unittest.main()
