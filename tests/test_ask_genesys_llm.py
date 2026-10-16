"""Ask Genesys LLM router + advanced script safety tests (no network, no API key needed).

Run:  python -m unittest discover -s tests -p "test_ask_genesys*.py" -v
Optional live test: set OPENAI_API_KEY and ASK_GENESYS_LIVE_TEST=1.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ask_genesys import advanced, llm_parser  # noqa: E402
from app.ask_genesys.script_safety import check_script  # noqa: E402
from tools.reporting import ask_genesys_router as router  # noqa: E402


def fake_tool_response(name: str, arguments: dict | str):
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"choices": [{"message": {"tool_calls": [{"function": {"name": name, "arguments": args}}]}}]}


def chat_returning(name: str, arguments: dict | str):
    return lambda **_kwargs: fake_tool_response(name, arguments)


GOOD_SCRIPT = '''from core.cli import create_client, tool_log
import json

client = create_client(log=tool_log)
data = client.request("GET", "/api/v2/routing/queues", params={"pageSize": 5})
print(json.dumps([q["name"] for q in data.get("entities", [])], indent=2))
body = {"interval": "2026-01-01T00:00:00.000Z/2026-01-02T00:00:00.000Z"}
client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
'''


class LLMRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        router._QUIET = True

    def test_last_inbound_call_routes_to_existing_handler(self):
        chat = chat_returning("last_inbound_call", {"phone": "(954) 790-9709", "lookback_days": 30})
        parsed, meta = router.resolve_intent("when did (954) 790-9709 last call?", chat_fn=chat)
        self.assertEqual(parsed.intent, "last_inbound_call")
        self.assertEqual(parsed.phone, "9547909709")
        self.assertEqual(parsed.lookback_days, 30)
        self.assertEqual(meta["source"], "llm")
        self.assertTrue(meta["params"]["phone"].startswith("***"))  # masked in transparency payload

    def test_user_queue_membership_maps_to_internal_intent(self):
        chat = chat_returning("user_queue_membership", {"user": "Jane Doe", "include_unjoined": True})
        parsed, meta = router.resolve_intent("which queues is Jane Doe a member of", chat_fn=chat)
        self.assertEqual(parsed.intent, "user_queues_joined")
        self.assertEqual(parsed.user_query, "Jane Doe")
        self.assertTrue(parsed.include_unjoined)
        self.assertEqual(meta["tool"], "user_queue_membership")

    def test_help_and_need_clarification(self):
        parsed, _ = router.resolve_intent("what can you do", chat_fn=chat_returning("help", {}))
        self.assertEqual(parsed.intent, "help")
        parsed, _ = router.resolve_intent(
            "delete all queues", chat_fn=chat_returning("need_clarification", {"question": "Read-only tool."})
        )
        self.assertEqual(parsed.intent, "help")
        self.assertEqual(parsed.notes, ["Read-only tool."])

    def test_unknown_tool_falls_back_to_regex(self):
        chat = chat_returning("delete_user", {"user": "x"})
        parsed, meta = router.resolve_intent("last call from 9547909709", chat_fn=chat)
        self.assertEqual(meta["source"], "regex")
        self.assertEqual(parsed.intent, "last_inbound_call")

    def test_hallucinated_phone_rejected(self):
        chat = chat_returning("last_inbound_call", {"phone": "2125551234"})
        parsed, meta = router.resolve_intent("when did my friend call?", chat_fn=chat)
        self.assertEqual(meta["source"], "regex")  # LLM phone not in prompt -> fallback
        self.assertEqual(parsed.intent, "help")

    def test_bad_json_arguments_fall_back(self):
        parsed, meta = router.resolve_intent("last call from 9547909709", chat_fn=chat_returning("last_inbound_call", "{nope"))
        self.assertEqual(meta["source"], "regex")

    def test_api_error_falls_back(self):
        def boom(**_kwargs):
            raise RuntimeError("rate limited")

        parsed, meta = router.resolve_intent("last call from 9547909709", chat_fn=boom)
        self.assertEqual(meta["source"], "regex")
        self.assertEqual(parsed.phone, "9547909709")

    def test_lookback_clamped(self):
        chat = chat_returning("last_inbound_call", {"phone": "9547909709", "lookback_days": 99999})
        parsed, _ = router.resolve_intent("last call from 9547909709", chat_fn=chat)
        self.assertEqual(parsed.lookback_days, 365)

    def test_no_api_key_uses_regex_without_calling_openai(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("ASK_GENESYS_USE_LLM", None)
            self.assertFalse(llm_parser.llm_enabled())
            parsed, meta = router.resolve_intent("What queues is jane.doe@example.com live on?")
        self.assertEqual(meta["source"], "regex")
        self.assertEqual(parsed.intent, "user_queues_joined")

    def test_use_llm_flag(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("ASK_GENESYS_USE_LLM", None)
            self.assertTrue(llm_parser.llm_enabled())
            os.environ["ASK_GENESYS_USE_LLM"] = "0"
            self.assertFalse(llm_parser.llm_enabled())

    def test_answer_prompt_includes_parsed_intent(self):
        result = router.answer_prompt(
            lambda: (_ for _ in ()).throw(AssertionError("help must not hit Genesys")),
            "help",
            chat_fn=chat_returning("help", {}),
        )
        self.assertEqual(result["parsed_intent"]["source"], "llm")
        self.assertTrue(result["read_only"])

    @unittest.skipUnless(
        os.environ.get("OPENAI_API_KEY") and os.environ.get("ASK_GENESYS_LIVE_TEST") == "1",
        "set OPENAI_API_KEY and ASK_GENESYS_LIVE_TEST=1 for the live OpenAI test",
    )
    def test_live_openai_routing(self):
        parsed, meta = router.resolve_intent("hey when was the last time 954-790-9709 rang us up")
        self.assertEqual(meta["source"], "llm")
        self.assertEqual(parsed.intent, "last_inbound_call")
        self.assertEqual(parsed.phone, "9547909709")


class ScriptSafetyTests(unittest.TestCase):
    def assertBlocked(self, code: str, needle: str = ""):
        result = check_script(code)
        self.assertFalse(result.ok, f"expected block, got pass for:\n{code}")
        if needle:
            self.assertTrue(any(needle in v for v in result.violations), result.violations)

    def test_good_script_passes(self):
        result = check_script(GOOD_SCRIPT)
        self.assertTrue(result.ok, result.violations)

    def test_blocks_import_os(self):
        self.assertBlocked("import os\n" + GOOD_SCRIPT, "import 'os'")

    def test_blocks_subprocess_and_from_imports(self):
        self.assertBlocked("import subprocess\n" + GOOD_SCRIPT)
        self.assertBlocked("from pathlib import Path\n" + GOOD_SCRIPT)
        self.assertBlocked("import requests\n" + GOOD_SCRIPT)
        self.assertBlocked("from core.cli import write_csv\n" + GOOD_SCRIPT)

    def test_blocks_patch_post_delete(self):
        self.assertBlocked(GOOD_SCRIPT + 'client.request("PATCH", "/api/v2/users/abc", json_body={})\n', "PATCH")
        self.assertBlocked(GOOD_SCRIPT + 'client.request("DELETE", "/api/v2/users/abc")\n', "DELETE")
        self.assertBlocked(GOOD_SCRIPT + 'client.request("PUT", "/api/v2/users/abc", json_body={})\n', "PUT")
        self.assertBlocked(GOOD_SCRIPT + 'client.request("POST", "/api/v2/users", json_body={})\n', "POST only allowed")
        self.assertBlocked(GOOD_SCRIPT + 'client.request(\n    "patch", "/api/v2/users/x")\n', "PATCH")

    def test_blocks_dynamic_method_and_aliasing(self):
        self.assertBlocked(GOOD_SCRIPT + 'm = "PA" + "TCH"\nclient.request(m, "/api/v2/users/x")\n', "literal")
        self.assertBlocked(GOOD_SCRIPT + 'r = client.request\nr("PATCH", "/api/v2/users/x")\n', "aliasing")
        self.assertBlocked(GOOD_SCRIPT + 'client.request(**{"method": "PATCH"})\n')

    def test_blocks_open_eval_exec_getattr(self):
        self.assertBlocked(GOOD_SCRIPT + 'open("x.txt", "w").write("hi")\n', "open")
        self.assertBlocked(GOOD_SCRIPT + 'eval("1+1")\n', "eval")
        self.assertBlocked(GOOD_SCRIPT + 'exec("print(1)")\n', "exec")
        self.assertBlocked(GOOD_SCRIPT + 'getattr(client, "request")("PATCH", "/x")\n', "getattr")
        self.assertBlocked(GOOD_SCRIPT + '__import__("os")\n')

    def test_blocks_token_leak_and_dunder(self):
        self.assertBlocked(GOOD_SCRIPT + "print(client.token)\n", ".token")
        self.assertBlocked(GOOD_SCRIPT + "print(client.__class__)\n", "dunder")
        self.assertBlocked(GOOD_SCRIPT + 'print("{0.__class__}".format(client))\n')

    def test_requires_create_client(self):
        self.assertBlocked("import json\nprint(json.dumps({}))\n", "create_client")

    def test_syntax_error_and_empty(self):
        self.assertBlocked("def (:\n")
        self.assertBlocked("   ")


class AdvancedFlowTests(unittest.TestCase):
    def _chat(self, script: str):
        content = json.dumps({"summary": "Lists queues.", "script": script})
        return lambda **_kw: {"choices": [{"message": {"content": content}}]}

    def test_unsafe_script_is_not_executed(self):
        called = []
        result = advanced.answer_advanced(
            "patch a user",
            user="tester",
            chat_fn=self._chat('import os\nfrom core.cli import create_client\nclient = create_client()\n'),
            runner=lambda *a, **k: called.append(1) or {},
        )
        self.assertEqual(called, [])
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["error"], "blocked for safety")
        self.assertIn("import os", result["script"])
        self.assertFalse(result["safety"]["passed"])

    def test_patch_script_is_blocked(self):
        result = advanced.answer_advanced(
            "x",
            chat_fn=self._chat(GOOD_SCRIPT + 'client.request("PATCH", "/api/v2/users/1", json_body={})\n'),
            runner=lambda *a, **k: self.fail("must not run"),
        )
        self.assertTrue(result["blocked"])

    def test_safe_script_runs_via_runner(self):
        seen = {}

        def fake_runner(script, *, stamp):
            seen["script"] = script
            return {"returncode": 0, "stdout": "ok\n", "stderr": "", "timed_out": False, "duration_sec": 0.1}

        result = advanced.answer_advanced("list queues", chat_fn=self._chat(GOOD_SCRIPT), runner=fake_runner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["execution"]["stdout"], "ok\n")
        self.assertEqual(result["parsed_intent"]["source"], "llm_advanced")
        self.assertIn("create_client", seen["script"])

    def test_role_gate(self):
        from app.ops_auth import SESSION_ROLES_KEY

        env = {"OPENAI_API_KEY": "sk-test", "OPS_QUEUE_PERFORMANCE_PUBLIC": "0"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("ASK_GENESYS_ADVANCED_ROLES", None)
            os.environ.pop("OPS_QUEUE_PERFORMANCE_ROLES", None)
            os.environ.pop("ASK_GENESYS_ADVANCED_ENABLED", None)
            self.assertTrue(advanced.advanced_access({SESSION_ROLES_KEY: ["Supervisor"]})[0])
            self.assertTrue(advanced.advanced_access({SESSION_ROLES_KEY: ["Admin"]})[0])
            self.assertFalse(advanced.advanced_access({SESSION_ROLES_KEY: ["Agent"]})[0])
            self.assertFalse(advanced.advanced_access({})[0])
            os.environ["ASK_GENESYS_ADVANCED_ROLES"] = "admin"
            self.assertFalse(advanced.advanced_access({SESSION_ROLES_KEY: ["Supervisor"]})[0])
            self.assertTrue(advanced.advanced_access({SESSION_ROLES_KEY: ["Admin"]})[0])

    def test_role_gate_realistic_genesys_role_strings(self):
        from app.ops_auth import SESSION_ROLES_KEY

        env = {"OPENAI_API_KEY": "sk-test", "OPS_QUEUE_PERFORMANCE_PUBLIC": "0"}
        with mock.patch.dict(os.environ, env, clear=False):
            for name in ("ASK_GENESYS_ADVANCED_ROLES", "OPS_QUEUE_PERFORMANCE_ROLES", "ASK_GENESYS_ADVANCED_ENABLED"):
                os.environ.pop(name, None)
            ok = advanced.advanced_access
            self.assertTrue(ok({SESSION_ROLES_KEY: ["Employee", "Master Admin"]})[0])
            self.assertTrue(ok({SESSION_ROLES_KEY: ["  ADMIN "]})[0])
            self.assertTrue(ok({SESSION_ROLES_KEY: ["Super_Admin"]})[0])
            self.assertFalse(ok({SESSION_ROLES_KEY: ["Employee", "Archwell Agent"]})[0])
            # custom role list (spacing/case/separators tolerated)
            os.environ["ASK_GENESYS_ADVANCED_ROLES"] = " Archwell-Admin , admin "
            self.assertTrue(ok({SESSION_ROLES_KEY: ["Archwell Admin"]})[0])
            allowed, reason = ok({SESSION_ROLES_KEY: ["Employee"]})
            self.assertFalse(allowed)
            self.assertIn("Employee", reason)  # denial reason shows the user's actual roles

    def test_denied_without_openai_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            self.assertFalse(advanced.advanced_access({})[0])


if __name__ == "__main__":
    unittest.main()
