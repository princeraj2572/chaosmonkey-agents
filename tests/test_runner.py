import json
import unittest

from chaosmonkey.examples.inventory import TRUE_TOTAL, check, make_inventory
from chaosmonkey.judge import Outcome
from chaosmonkey.report import render_json
from chaosmonkey.runner import Runner


class RunnerTests(unittest.TestCase):
    def test_baseline_and_all_named_faults_are_recorded(self):
        agent, tools = make_inventory("careful")
        result = Runner().run(agent, tools, seeds=list(range(9)), example="inventory", prompt="task", check=check)
        self.assertEqual(result.baseline.outcome, Outcome.UNAFFECTED)
        self.assertEqual([run.fault for run in result.chaos_runs], [
            "empty", "truncate", "noise", "broken_json", "wrong_numbers",
            "error", "timeout", "injection", "duplicate",
        ])
        self.assertTrue(all(run.injected for run in result.chaos_runs))
        self.assertEqual(result.baseline.output.final, f"The total number of units in stock is {TRUE_TOTAL}.")

    def test_same_seed_is_byte_stable(self):
        first_agent, first_tools = make_inventory("naive")
        second_agent, second_tools = make_inventory("naive")
        first = render_json(Runner().run(first_agent, first_tools, seeds=[7, 8, 9], example="inventory", prompt="task", check=check))
        second = render_json(Runner().run(second_agent, second_tools, seeds=[7, 8, 9], example="inventory", prompt="task", check=check))
        self.assertEqual(json.loads(first), json.loads(second))

    def test_zero_rate_runs_clean_trials(self):
        agent, tools = make_inventory("careful")
        result = Runner(fault_rate=0).run(agent, tools, seeds=[1], example="inventory", prompt="task", check=check)
        self.assertFalse(result.chaos_runs[0].injected)
        self.assertEqual(result.chaos_runs[0].outcome, Outcome.NOT_INJECTED)


if __name__ == "__main__":
    unittest.main()
