import unittest
from repair_demo import EVENTS, FakeCRM, UpstreamError, broken_flow, repaired_flow

class RepairTests(unittest.TestCase):
    def test_old_schema_reproduces_failure(self):
        with self.assertRaises(KeyError): broken_flow(EVENTS)
    def test_normalizes_and_quarantines(self):
        crm = FakeCRM()
        audit = repaired_flow(EVENTS, crm)
        self.assertEqual(len(crm.rows), 2)
        self.assertEqual(crm.rows['evt-101']['email'], 'ada@example.test')
        self.assertEqual(sum(a['status'] == 'quarantined' for a in audit), 2)
    def test_duplicate_and_replay(self):
        crm, ledger = FakeCRM(), set()
        repaired_flow(EVENTS, crm, ledger)
        repaired_flow(EVENTS, crm, ledger)
        self.assertEqual(crm.attempts, {'evt-101': 1, 'evt-102': 1})
    def test_transient_retry_recovers(self):
        crm = FakeCRM({'evt-102': 1})
        repaired_flow(EVENTS, crm)
        self.assertEqual(crm.attempts['evt-102'], 2)
        self.assertEqual(len(crm.rows), 2)
    def test_retry_budget_does_not_mark_failure_delivered(self):
        crm, ledger = FakeCRM({'evt-101': 10}), set()
        audit = repaired_flow(EVENTS[:1], crm, ledger)
        self.assertEqual(crm.attempts['evt-101'], 3)
        self.assertNotIn('evt-101', ledger)
        self.assertEqual(audit[-1]['status'], 'failed')
    def test_permanent_error_not_retried(self):
        class UnauthorizedCRM(FakeCRM):
            def create(self, key, lead):
                self.attempts[key] = self.attempts.get(key, 0) + 1
                raise UpstreamError(401)
        crm = UnauthorizedCRM()
        audit = repaired_flow(EVENTS[:1], crm)
        self.assertEqual(crm.attempts['evt-101'], 1)
        self.assertEqual(audit[-1]['code'], 401)
    def test_invalid_envelope(self):
        audit = repaired_flow([{}, {'event_id': 'x', 'lead': None}], FakeCRM())
        self.assertTrue(all(a['status'] == 'quarantined' for a in audit))
    def test_invalid_retry_budget(self):
        with self.assertRaises(ValueError): repaired_flow([], FakeCRM(), max_attempts=0)

if __name__ == '__main__': unittest.main()
