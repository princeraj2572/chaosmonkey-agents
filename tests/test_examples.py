import unittest

from chaosmonkey.examples.inventory import check as inventory_check, make_inventory
from chaosmonkey.examples.research import check as research_check, make_research
from chaosmonkey.judge import Outcome
from chaosmonkey.runner import Runner


class ExampleTests(unittest.TestCase):
    def test_careful_examples_recover(self):
        for name, factory, check in (("inventory", make_inventory, inventory_check), ("research", make_research, research_check)):
            with self.subTest(name=name):
                agent, tools = factory("careful")
                result = Runner().run(agent, tools, seeds=list(range(18)), example=name, prompt=name, check=check)
                self.assertEqual(result.baseline.outcome, Outcome.UNAFFECTED)
                self.assertEqual(result.silent_failure_rate, 0.0)
                self.assertGreater(sum(run.outcome is Outcome.RECOVERED for run in result.chaos_runs), 0)

    def test_naive_examples_have_dangerous_runs(self):
        for name, factory, check in (("inventory", make_inventory, inventory_check), ("research", make_research, research_check)):
            with self.subTest(name=name):
                agent, tools = factory("naive")
                result = Runner().run(agent, tools, seeds=list(range(18)), example=name, prompt=name, check=check)
                self.assertGreater(result.silent_failure_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
