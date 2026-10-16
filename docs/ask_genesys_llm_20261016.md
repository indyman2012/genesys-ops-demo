# Ask Genesys — OpenAI routing + Advanced query (2026-10-16)

Page: `/ask-genesys` · API: `POST /api/ask-genesys` · Blueprint: `app/ask_genesys/` (registered by `agent_app_demo.py`).

## 1. Standard mode (everyone with Queue Performance access)

`app/ask_genesys/llm_parser.py` sends the question to OpenAI chat completions with **function calling**
(`tool_choice="required"`). The model can only pick one of four tools:

| Tool | Maps to handler |
|---|---|
| `last_inbound_call` | `answer_last_inbound_call` |
| `user_queue_membership` | `answer_user_queues` (`user_queues_joined`) |
| `help` | help result |
| `need_clarification` | help result with the model's message |

Safety layers in `tools/reporting/ask_genesys_router.py`:

1. Tool name must be in the allowlist, otherwise → regex fallback.
2. Parameters are re-validated (NANP phone, phone digits must literally appear in the prompt, lookback clamped to 1–365, user string length-limited).
3. Any OpenAI error / missing key / `openai` package missing → regex parser (the previous behaviour).
4. Stdout log per request: `[ASK] route=llm|regex intent=... params={...}` (phone masked to last 4 digits; **no** Genesys API responses logged).
5. The response includes `parsed_intent` (`source`, `model`, `tool`, `intent`, `params`) — visible under **View raw JSON**.

The model never sees Genesys data and cannot call anything but the handlers above.

## 2. Advanced mode (admin/supervisor only)

Checkbox **Advanced query (generate read-only script)** (shown only when permitted) or `POST /api/ask-genesys {"prompt": "...", "advanced": true}`.

Flow (`app/ask_genesys/advanced.py`, `script_safety.py`):

1. OpenAI generates a Python script (JSON `{summary, script}`) following the toolkit pattern (`create_client`, `client.request("GET", ...)`, analytics query POST).
2. **Static AST safety check** — deny by default:
   * imports only: `json, datetime, re, math, collections, itertools, statistics, typing`, plus `from core.cli import create_client, tool_log`, `from core.genesys_client import GenesysClient, GenesysError, safe_str`, and selected read-only `core.lookups` helpers;
   * blocks `os/sys/subprocess/pathlib/requests`, `open`, `eval/exec/compile/__import__/getattr`, dunder access, `write_csv`, `client.token`, `client.post/put/patch/delete`, aliasing `client.request`;
   * `client.request` must use a **literal** method: `GET` (any `/api/v2/...`), or `POST` only to `/api/v2/analytics/.../*query` or `/api/v2/.../search`; `PUT/PATCH/DELETE` blocked;
   * must import and use `create_client`.
3. If it passes → saved to `outputs/ask_genesys_runs/run_<stamp>.py` and executed in a **subprocess** (`sys.executable`, cwd = project root, 120 s timeout, stdin closed, `OPENAI_API_KEY`/`FLASK_SECRET_KEY` removed from the child env, output capped at 30k chars).
4. If it fails the check → **not executed**; response has `blocked: true`, `error: "blocked for safety"`, the script, and the violations.
5. Every generated script (pass or blocked) is saved to `outputs/ask_genesys_generated/ask_<stamp>_<ok|blocked>.py` with a comment header (user, model, prompt, safety result).

> The check is defense in depth, **not** a sandbox. The child process still uses the app's Genesys client-credentials OAuth client, so keep that client's roles read-only for this deployment where possible. `outputs/` is ephemeral on Render free tier and git-ignored — copy audit scripts elsewhere if you need long-term retention (or watch the `[ASK-ADV]` stdout log lines).

### Role gate
Allowed if the signed-in user's Genesys role names intersect `ASK_GENESYS_ADVANCED_ROLES` (comma list, case-insensitive). If unset, it uses the same list as Queue Performance (`OPS_QUEUE_PERFORMANCE_ROLES`, default `supervisor, admin, super admin`). For a stricter gate use e.g. `ASK_GENESYS_ADVANCED_ROLES=admin,super admin`.
Advanced is also refused when `OPENAI_API_KEY` is missing, `ASK_GENESYS_ADVANCED_ENABLED=0`, or when Genesys sign-in is disabled (`OPS_QUEUE_PERFORMANCE_PUBLIC=1`) unless `ASK_GENESYS_ADVANCED_PUBLIC=1` (local dev only — never on Render).

## 3. Environment variables (Render → genesys-ops-demo-3 → Environment)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | yes (for LLM) | — | Secret. Set in Render only; never commit. |
| `ASK_GENESYS_USE_LLM` | no | `1` if key present, else off | `0` forces regex-only. |
| `ASK_GENESYS_OPENAI_MODEL` | no | `gpt-4o-mini` | Standard routing model. |
| `ASK_GENESYS_OPENAI_TIMEOUT` | no | `20` | Seconds per OpenAI call. |
| `ASK_GENESYS_ADVANCED_MODEL` | no | routing model | `gpt-4o` recommended for script quality. |
| `ASK_GENESYS_ADVANCED_ROLES` | no | `OPS_QUEUE_PERFORMANCE_ROLES` | e.g. `admin,super admin`. |
| `ASK_GENESYS_ADVANCED_ENABLED` | no | `1` | Kill switch. |
| `ASK_GENESYS_ADVANCED_PUBLIC` | no | `0` | Local dev only. |

Existing vars still apply: `GENESYS_CLIENT_ID/SECRET`, `GENESYS_REGION`, `GENESYS_AGENT_CLIENT_ID/SECRET`, `FLASK_SECRET_KEY`, `OPS_QUEUE_PERFORMANCE_ROLES`.
`requirements.txt` now includes `openai>=1.40.0` (Render reinstalls on deploy).

## 4. Example prompts

Standard:
* `hey when was the last time 954-790-9709 rang us up?`
* `Which queues is jane.doe@archwell.com live on?`
* `Is Jane Doe a member of any queues?`

Advanced (checkbox on):
* `List the 10 queues with the most members and their member counts`
* `For the last 7 days, count inbound voice conversations per queue and show the top 5`
* `Which users with the Supervisor role are not on any queue?`
* Negative test: `Change Jane Doe's division to X` → script prints "Unsupported: this tool is read-only." (or is blocked).

## 5. Tests

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python -m unittest discover -s tests -p "test_ask_genesys*.py" -v
# optional live OpenAI routing test
$env:OPENAI_API_KEY="sk-..."; $env:ASK_GENESYS_LIVE_TEST="1"
python -m unittest tests.test_ask_genesys_llm.LLMRouterTests.test_live_openai_routing -v
```

Local run:

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
pip install -r requirements.txt
$env:OPENAI_API_KEY="sk-..."; $env:OPS_QUEUE_PERFORMANCE_PUBLIC="1"; $env:ASK_GENESYS_ADVANCED_PUBLIC="1"
python agent_app_demo.py   # http://127.0.0.1:8082/ask-genesys
```
