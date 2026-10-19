import unittest

from chaosmonkey.llm import AnthropicClient, OpenAIClient


class LLMTests(unittest.TestCase):
    def test_anthropic_request_parts(self):
        client = AnthropicClient(api_key="secret", model="model", base_url="https://example.test")
        payload, headers, url = client._request_parts([{"role": "system", "content": "be safe"}, {"role": "user", "content": "hi"}])
        self.assertEqual(url, "https://example.test/v1/messages")
        self.assertEqual(payload["system"], "be safe")
        self.assertEqual(headers["x-api-key"], "secret")

    def test_openai_request_parts(self):
        client = OpenAIClient(api_key="secret", model="model", base_url="https://example.test")
        payload, headers, url = client._request_parts([{"role": "user", "content": "hi"}])
        self.assertEqual(url, "https://example.test/v1/chat/completions")
        self.assertEqual(payload["model"], "model")
        self.assertEqual(headers["authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()
