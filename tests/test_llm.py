import json
import os
import socket
import subprocess
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from course_compass.llm import (DEFAULT_BASE_URL, DEFAULT_CLAUDE_MODEL, DEFAULT_MODEL, ClaudeCLIClient,
                                ClaudeCLIConfig, LLMClient, LLMConfig, LLMError, LLMHTTPError,
                                LLMProviderError, LLMResponseError, LLMTimeoutError, MissingKeyError,
                                ProviderUnavailableError, check_base_url, claude_available, key_available,
                                redact, serialize_messages)
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


# ---------------------------------------------------------------- Claude CLI provider

CLAUDE = ClaudeCLIConfig(binary="/opt/bin/claude", model="sonnet", timeout=9)
SYSTEM_AND_USER = [{"role": "system", "content": "Be strict."}, {"role": "user", "content": "Question?"}]


def cli_result(result="hello", is_error=False, returncode=0, stderr="", **extra):
    body = {"type": "result", "subtype": "success" if not is_error else "error_during_execution",
            "is_error": is_error, "result": result, **extra}
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(body), stderr=stderr)


def fake_runner(result=None, error=None, seen=None):
    def runner(argv, stdin, timeout, env, cwd):
        if seen is not None:
            seen.append({"argv": argv, "stdin": stdin, "timeout": timeout, "env": env, "cwd": cwd})
        if error is not None:
            raise error
        return result
    return runner


class ClaudeConfigTests(unittest.TestCase):
    def test_defaults_resolve_binary_on_path(self):
        config = ClaudeCLIConfig.from_env({}, which=lambda name: f"/usr/bin/{name}")
        self.assertEqual((config.binary, config.model, config.timeout), ("/usr/bin/claude", DEFAULT_CLAUDE_MODEL, 120))

    def test_env_overrides(self):
        env = {"COURSE_COMPASS_CLAUDE_MODEL": "haiku", "COURSE_COMPASS_CLAUDE_BIN": "/x/claude",
               "COURSE_COMPASS_CLAUDE_TIMEOUT": "45"}
        config = ClaudeCLIConfig.from_env(env, which=lambda name: name)
        self.assertEqual((config.binary, config.model, config.timeout), ("/x/claude", "haiku", 45.0))

    def test_missing_binary_is_unavailable_with_clear_hint(self):
        with self.assertRaisesRegex(ProviderUnavailableError, "COURSE_COMPASS_CLAUDE_BIN"):
            ClaudeCLIConfig.from_env({}, which=lambda name: None)
        with mock.patch("course_compass.llm.shutil.which", return_value=None):
            self.assertFalse(claude_available({}))

    def test_bad_timeout(self):
        for value in ("soon", "0", "-3"):
            with self.assertRaises(LLMError):
                ClaudeCLIConfig.from_env({"COURSE_COMPASS_CLAUDE_TIMEOUT": value}, which=lambda n: n)


class SerializeTests(unittest.TestCase):
    def test_system_goes_to_system_prompt_and_single_user_is_verbatim(self):
        self.assertEqual(serialize_messages(SYSTEM_AND_USER), ("Be strict.", "Question?"))

    def test_multi_turn_keeps_roles(self):
        messages = SYSTEM_AND_USER + [{"role": "assistant", "content": "{bad}"},
                                      {"role": "user", "content": "Fix it."}]
        system, prompt = serialize_messages(messages)
        self.assertEqual(system, "Be strict.")
        self.assertIn('<turn role="user">\nQuestion?\n</turn>', prompt)
        self.assertIn('<turn role="assistant">\n{bad}\n</turn>', prompt)
        self.assertTrue(prompt.rstrip().endswith('<turn role="user">\nFix it.\n</turn>'))
        self.assertLess(prompt.index("Question?"), prompt.index("{bad}"))

    def test_last_turn_must_be_user(self):
        with self.assertRaises(LLMError):
            serialize_messages([{"role": "system", "content": "s"}])


class ClaudeChatTests(unittest.TestCase):
    def test_success_runs_print_mode_without_tools(self):
        seen = []
        client = ClaudeCLIClient(CLAUDE, runner=fake_runner(cli_result("answer"), seen=seen))
        with mock.patch.dict(os.environ, {"COURSE_COMPASS_API_KEY": "sk-secret-value-123"}):
            self.assertEqual(client.chat(SYSTEM_AND_USER, temperature=0.3), "answer")
        call = seen[0]
        argv = call["argv"]
        self.assertEqual(argv[:4], ["/opt/bin/claude", "-p", "--model", "sonnet"])
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "Be strict.")
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(call["stdin"], "Question?")       # notes travel on stdin, not argv
        self.assertEqual(call["timeout"], 9)
        self.assertNotIn("COURSE_COMPASS_API_KEY", call["env"])
        self.assertFalse(os.path.exists(call["cwd"]))     # throwaway working directory
        self.assertEqual(client.label, "Claude CLI: sonnet")

    def test_timeout_maps_to_timeout_error(self):
        error = subprocess.TimeoutExpired(cmd="claude", timeout=9)
        with self.assertRaisesRegex(LLMTimeoutError, "9s"):
            ClaudeCLIClient(CLAUDE, runner=fake_runner(error=error)).chat(SYSTEM_AND_USER)

    def test_binary_vanished_is_unavailable(self):
        with self.assertRaises(ProviderUnavailableError):
            ClaudeCLIClient(CLAUDE, runner=fake_runner(error=FileNotFoundError())).chat(SYSTEM_AND_USER)

    def test_not_logged_in_is_unavailable_with_login_hint(self):
        result = cli_result("Not logged in · Please run /login", is_error=True, returncode=1)
        with self.assertRaisesRegex(ProviderUnavailableError, "/login") as ctx:
            ClaudeCLIClient(CLAUDE, runner=fake_runner(result)).chat(SYSTEM_AND_USER)
        self.assertIsInstance(ctx.exception, LLMError)

    def test_api_error_is_provider_error(self):
        result = cli_result("API Error: 529 overloaded", is_error=True, returncode=1, api_error_status=529)
        with self.assertRaisesRegex(LLMProviderError, "529"):
            ClaudeCLIClient(CLAUDE, runner=fake_runner(result)).chat(SYSTEM_AND_USER)

    def test_non_json_output(self):
        ok_but_garbage = SimpleNamespace(returncode=0, stdout="plain text", stderr="")
        with self.assertRaises(LLMResponseError):
            ClaudeCLIClient(CLAUDE, runner=fake_runner(ok_but_garbage)).chat(SYSTEM_AND_USER)
        crashed = SimpleNamespace(returncode=2, stdout="", stderr="boom")
        with self.assertRaisesRegex(LLMProviderError, "code 2: boom"):
            ClaudeCLIClient(CLAUDE, runner=fake_runner(crashed)).chat(SYSTEM_AND_USER)

    def test_empty_result(self):
        with self.assertRaises(LLMResponseError):
            ClaudeCLIClient(CLAUDE, runner=fake_runner(cli_result("  "))).chat(SYSTEM_AND_USER)

    def test_errors_never_leak_secrets(self):
        leaky = SimpleNamespace(returncode=1, stdout="",
                                stderr="failed: Authorization: Bearer sk-fake-abcdefghijklmnop token=hunter2hunter2")
        with self.assertRaises(LLMError) as ctx:
            ClaudeCLIClient(CLAUDE, runner=fake_runner(leaky)).chat(SYSTEM_AND_USER)
        message = str(ctx.exception)
        for secret in ("sk-fake-abcdefghijklmnop", "hunter2hunter2"):
            self.assertNotIn(secret, message)

    def test_redact_trims_and_masks(self):
        self.assertEqual(redact("key: abc123"), "key: ***")
        self.assertNotIn("sk-1234567890", redact("oops sk-1234567890"))
        self.assertLessEqual(len(redact("x " * 1000)), 300)


if __name__ == "__main__":
    unittest.main()
