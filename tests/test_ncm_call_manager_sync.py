import unittest
from io import BytesIO

from openpyxl import Workbook

from core.datatables import build_call_manager_row_body, find_row_by_dnis, schema_property_key
from tools.queues.ncm_call_manager_sync_lib import (
    build_roster_template_xlsx,
    parse_ncm_roster_workbook,
    resolve_patient_services_center_queue,
)


class NcmCallManagerSyncTests(unittest.TestCase):
    def _workbook_bytes(self, rows: list[tuple]) -> bytes:
        wb = Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_roster_template_xlsx(self):
        data = build_roster_template_xlsx()
        self.assertTrue(data[:2] == b"PK")
        rows = parse_ncm_roster_workbook(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["queue_name"], "NCM_BHM_Roebuck")

    def test_parse_roster_workbook(self):
        data = self._workbook_bytes(
            [
                ("Email Address", "Queue Name", "Phone Number"),
                ("agent@example.com", "NCM_BHM_Roebuck", "2055551212"),
            ]
        )
        rows = parse_ncm_roster_workbook(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "agent@example.com")
        self.assertEqual(rows[0]["queue_name"], "NCM_BHM_Roebuck")
        self.assertEqual(rows[0]["phone"], "2055551212")

    def test_resolve_patient_services_center_queue(self):
        queues = [{"name": "Patient Services - BHM Roebuck"}, {"name": "NCM_BHM_Roebuck"}]
        name = resolve_patient_services_center_queue("NCM_BHM_Roebuck", queues)
        self.assertEqual(name, "Patient Services - BHM Roebuck")

    def test_datatable_schema_helpers(self):
        schema = {
            "properties": {
                "key": {"title": "DNIS"},
                "queueName": {"title": "QueueName"},
                "skill": {"title": "Skill"},
                "centerQueue": {"title": "CenterQueue"},
                "open": {"title": "Open"},
            }
        }
        self.assertEqual(schema_property_key(schema, "dnis"), "key")
        body = build_call_manager_row_body(
            schema,
            dnis_e164="+12055551212",
            queue_name="NCM_BHM_Roebuck",
            center_queue_name="Patient Services - BHM Roebuck",
        )
        self.assertEqual(body["key"], "+12055551212")
        self.assertEqual(body["queueName"], "NCM_BHM_Roebuck")
        self.assertEqual(body["skill"], "NCM")
        self.assertTrue(body["open"])

    def test_find_row_by_dnis(self):
        rows = [{"key": "+12055551212", "dnis": "+12055551212"}]
        hit = find_row_by_dnis(rows, "+12055551212", dnis_field="dnis")
        self.assertIsNotNone(hit)


if __name__ == "__main__":
    unittest.main()
