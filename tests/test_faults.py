import json
import random
import unittest

from chaosmonkey.faults import CANARY_WORD, FAULTS, ToolFault, apply_fault


class FaultTests(unittest.TestCase):
    def test_faults_are_deterministic_for_a_seed(self):
        text = '{"count": 12, "name": "alpha"}'
        for name in FAULTS:
            if name in {"error", "timeout"}:
                continue
            self.assertEqual(apply_fault(name, text, random.Random(9)), apply_fault(name, text, random.Random(9)))

    def test_text_faults_have_expected_shapes(self):
        text = '{"count": 12, "name": "alpha"}'
        self.assertEqual(apply_fault("empty", text, random.Random(1)), "")
        self.assertTrue(text.startswith(apply_fault("truncate", text, random.Random(1))))
        with self.assertRaises(json.JSONDecodeError):
            json.loads(apply_fault("broken_json", text, random.Random(1)))
        injected = apply_fault("injection", text, random.Random(1))
        self.assertIn(CANARY_WORD, injected)
        self.assertIn(text, injected)
        self.assertEqual(apply_fault("duplicate", text, random.Random(1)), text * 2)

    def test_loud_faults_raise(self):
        for name in ("error", "timeout"):
            with self.subTest(name=name):
                with self.assertRaises(ToolFault):
                    apply_fault(name, "data", random.Random(1))

    def test_unknown_fault_is_clear(self):
        with self.assertRaisesRegex(KeyError, "unknown fault"):
            apply_fault("does-not-exist", "data", random.Random(1))


if __name__ == "__main__":
    unittest.main()
