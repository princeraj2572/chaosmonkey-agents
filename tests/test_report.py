import unittest

from chaosmonkey.examples.research import check, make_research
from chaosmonkey.report import render_compare_html, render_html, render_json, render_terminal
from chaosmonkey.runner import Runner


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        agent, tools = make_research("naive")
        cls.result = Runner().run(agent, tools, seeds=list(range(9)), example="research", prompt="task", check=check)

    def test_renderers_include_rate_and_are_serialisable(self):
        terminal = render_terminal(self.result)
        html = render_html(self.result)
        payload = render_json(self.result)
        self.assertIn("silent failure rate", terminal)
        self.assertIn("silent failure rate", html)
        self.assertIn('"runs"', payload)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_mock_models_are_labelled_as_scripted(self):
        data = self.result.to_dict()
        data["metadata"] = {"model": "mock-naive"}
        self.assertIn("scripted stand-ins", render_terminal(data))
        self.assertIn("scripted stand-ins", render_html(data))
        data["metadata"] = {"model": "anthropic:x"}
        self.assertNotIn("scripted stand-ins", render_terminal(data))

    def test_comparison_contains_delta(self):
        html = render_compare_html(self.result.to_dict(), self.result.to_dict(), left_name="naive", right_name="careful")
        self.assertIn("Delta", html)
        self.assertIn("silent failure rate", html)


if __name__ == "__main__":
    unittest.main()
