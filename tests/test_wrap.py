import unittest

from chaosmonkey.wrap import ChaosPlan, ChaosSession


class WrapTests(unittest.TestCase):
    def test_clean_session_preserves_serialised_result(self):
        session = ChaosSession(ChaosPlan(None, call_index=0, seed=1))
        wrapped = session.wrap(lambda: {"ok": True}, "lookup")
        self.assertEqual(wrapped(), '{"ok": true}')
        self.assertFalse(session.injected)
        self.assertIsNone(session.records[0].fault)

    def test_fault_hits_only_planned_call(self):
        session = ChaosSession(ChaosPlan("empty", call_index=1, seed=1))
        wrapped = session.wrap(lambda value: value, "tool")
        self.assertEqual(wrapped("first"), "first")
        self.assertEqual(wrapped("second"), "")
        self.assertEqual(wrapped("third"), "third")
        self.assertTrue(session.injected)
        self.assertEqual([record.fault for record in session.records], [None, "empty", None])

    def test_out_of_range_plan_is_not_injected(self):
        session = ChaosSession(ChaosPlan("error", call_index=4, seed=1))
        wrapped = session.wrap(lambda: "ok", "tool")
        self.assertEqual(wrapped(), "ok")
        self.assertFalse(session.injected)

    def test_raising_fault_is_recorded(self):
        session = ChaosSession(ChaosPlan("error", call_index=0, seed=1))
        wrapped = session.wrap(lambda: "ok", "tool")
        with self.assertRaises(Exception):
            wrapped()
        self.assertEqual(session.records[0].fault, "error")
        self.assertIsNotNone(session.records[0].raised)


if __name__ == "__main__":
    unittest.main()
