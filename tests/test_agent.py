import unittest

from chaosmonkey.agent import Agent, AgentResult, parse_response


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, messages):
        return next(self.responses)


class AgentTests(unittest.TestCase):
    def test_call_then_final(self):
        agent = Agent(ScriptedModel(['CALL add {"a": 2, "b": 3}', "FINAL: done"]), {"add": lambda a, b: a + b})
        result = agent.run("calculate")
        self.assertTrue(result.success)
        self.assertEqual(result.final, "done")
        self.assertEqual(result.tool_calls[0].result, 5)
        self.assertEqual(result.messages[2]["role"], "tool")

    def test_unknown_tool_is_returned_to_model(self):
        agent = Agent(ScriptedModel(["CALL missing {}", "FINAL: unable"]), {})
        result = agent.run("try")
        self.assertTrue(result.success)
        self.assertIn("unknown tool", result.messages[2]["content"])

    def test_plain_reply_is_final(self):
        result = Agent(ScriptedModel(["A plain answer"])).run("question")
        self.assertEqual(result.final, "A plain answer")
        self.assertTrue(result.finished)

    def test_max_steps(self):
        result = Agent(ScriptedModel(["CALL add {}"] * 3), {"add": lambda: 1}, max_steps=2).run("loop")
        self.assertEqual(result.error, "max_steps")
        self.assertFalse(result.finished)

    def test_parser_supports_verbose_protocol(self):
        parsed = parse_response('TOOL: add\nINPUT: {"a": 1}')
        self.assertEqual(parsed.kind, "tool")
        self.assertEqual(parsed.arguments, {"a": 1})


if __name__ == "__main__":
    unittest.main()
