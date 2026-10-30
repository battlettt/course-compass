import json
import socket
import unittest
import urllib.error

from course_compass.llm import (DEFAULT_BASE_URL, DEFAULT_MODEL, LLMClient, LLMConfig, LLMError,
                                LLMHTTPError, LLMResponseError, LLMTimeoutError, MissingKeyError,
                                check_base_url, key_available)
from tests.helpers import FakeResponse, chat_body, fake_opener

CONFIG = LLMConfig(api_key="sk-test-123", model="m", base_url="https://llm.example/v1", timeout=7)
MESSAGES = [{"role": "user", "content": "hi"}]


class ConfigTests(unittest.TestCase):
    def test_missing_key(self):
        with self.assertRaises(MissingKeyError):
            LLMConfig.from_env({})
        with self.assertRaises(MissingKeyError):
            LLMConfig.from_env({"COURSE_COMPASS_API_KEY": "   "})
        self.assertFalse(key_available({}))

    def test_defaults_and_overrides(self):
        config = LLMConfig.from_env({"COURSE_COMPASS_API_KEY": "k"})
        self.assertEqual((config.model, config.base_url), (DEFAULT_MODEL, DEFAULT_BASE_URL))
        config = LLMConfig.from_env({"COURSE_COMPASS_API_KEY": "k", "COURSE_COMPASS_MODEL": "x",
                                     "COURSE_COMPASS_BASE_URL": "http://localhost:11434/v1/"})
        self.assertEqual((config.model, config.base_url), ("x", "http://localhost:11434/v1"))

    def test_repr_hides_key(self):
        self.assertNotIn("sk-test-123", repr(CONFIG))

    def test_base_url_must_be_https_unless_localhost(self):
        check_base_url("https://api.example.com/v1")
        check_base_url("http://127.0.0.1:8000/v1")
        for bad in ("http://evil.example/v1", "file:///etc/passwd", "ftp://x"):
            with self.assertRaises(LLMError):
                check_base_url(bad)


class ChatTests(unittest.TestCase):
    def test_success_sends_expected_request(self):
        seen = []
        client = LLMClient(CONFIG, opener=fake_opener(FakeResponse(chat_body("hello")), seen=seen))
        self.assertEqual(client.chat(MESSAGES), "hello")
        request, timeout = seen[0]
        self.assertEqual(request.full_url, "https://llm.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test-123")
        self.assertEqual(timeout, 7)
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "m")
        self.assertEqual(payload["messages"], MESSAGES)

    def test_http_error_status(self):
        error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        client = LLMClient(CONFIG, opener=fake_opener(error=error))
        with self.assertRaisesRegex(LLMHTTPError, "401") as ctx:
            client.chat(MESSAGES)
        self.assertNotIn("sk-test-123", str(ctx.exception))

    def test_non_200_status(self):
        client = LLMClient(CONFIG, opener=fake_opener(FakeResponse(b"{}", status=503)))
        with self.assertRaisesRegex(LLMHTTPError, "503"):
            client.chat(MESSAGES)

    def test_timeout(self):
        client = LLMClient(CONFIG, opener=fake_opener(error=socket.timeout("timed out")))
        with self.assertRaises(LLMTimeoutError):
            client.chat(MESSAGES)

    def test_timeout_wrapped_in_urlerror(self):
        client = LLMClient(CONFIG, opener=fake_opener(error=urllib.error.URLError(TimeoutError())))
        with self.assertRaises(LLMTimeoutError):
            client.chat(MESSAGES)

    def test_unreachable_host(self):
        client = LLMClient(CONFIG, opener=fake_opener(error=urllib.error.URLError("no route")))
        with self.assertRaisesRegex(LLMHTTPError, "Could not reach"):
            client.chat(MESSAGES)

    def test_bad_json_body(self):
        client = LLMClient(CONFIG, opener=fake_opener(FakeResponse(b"<html>oops</html>")))
        with self.assertRaises(LLMResponseError):
            client.chat(MESSAGES)

    def test_wrong_shape_and_empty_content(self):
        client = LLMClient(CONFIG, opener=fake_opener(FakeResponse(b'{"choices": []}')))
        with self.assertRaises(LLMResponseError):
            client.chat(MESSAGES)
        client = LLMClient(CONFIG, opener=fake_opener(FakeResponse(chat_body("  "))))
        with self.assertRaises(LLMResponseError):
            client.chat(MESSAGES)


if __name__ == "__main__":
    unittest.main()
