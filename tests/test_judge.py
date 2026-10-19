import unittest

from chaosmonkey.faults import CANARY_WORD
from chaosmonkey.judge import Outcome, classify
from chaosmonkey.wrap import ChaosPlan, ChaosSession, ToolCallRecord


class JudgeTests(unittest.TestCase):
    def session(self, fault="empty", retried=False):
        session = ChaosSession(ChaosPlan(fault, 0, 1))
        session.injected = True
        session.records = [ToolCallRecord("tool", {"x": 1}, "good", "", fault, None)]
        if retried:
            session.records.append(ToolCallRecord("tool", {"x": 1}, "good", "good", None, None))
        return session

    def test_outcomes(self):
        check = lambda text: text == "42"
        cases = [
            ("42", self.session(retried=True), Outcome.RECOVERED),
            ("42", self.session(retried=False), Outcome.UNAFFECTED),
            ("unable to verify", self.session(retried=False), Outcome.FLAGGED),
            ("41", self.session(retried=False), Outcome.SILENT_FAILURE),
            (CANARY_WORD, self.session(retried=False), Outcome.HIJACKED),
        ]
        for final, session, expected in cases:
            with self.subTest(expected=expected):
                self.assertIs(classify({"final": final}, session, check).outcome, expected)

    def test_not_injected(self):
        session = self.session()
        session.injected = False
        self.assertIs(classify({"final": "42"}, session, lambda _: True).outcome, Outcome.NOT_INJECTED)


if __name__ == "__main__":
    unittest.main()
