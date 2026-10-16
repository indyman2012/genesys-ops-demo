"""Static safety check for LLM-generated Ask Genesys scripts (AST allowlist, deny by default).

A script passes only if it is a plain read-only Genesys query:
  * imports limited to an allowlist (stdlib helpers + core.cli.create_client/tool_log,
    core.genesys_client, selected read-only core.lookups helpers)
  * no file / process / network / dynamic-code primitives, no dunder access
  * every `client.request(...)` uses a literal method:
      GET                             -> any literal /api/v2/... path
      POST                            -> only analytics *query* or *search* endpoints (read-only)
      PUT / PATCH / DELETE / other    -> blocked
This is defense in depth together with subprocess isolation + timeout; it is not a sandbox.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

MAX_SCRIPT_CHARS = 20_000

ALLOWED_PLAIN_MODULES = frozenset(
    {
        "json",
        "datetime",
        "re",
        "math",
        "collections",
        "itertools",
        "statistics",
        "typing",
        "__future__",
    }
)

# module -> names that may be imported from it ("*" never allowed).
ALLOWED_FROM_IMPORTS: dict[str, frozenset[str]] = {
    "core.cli": frozenset({"create_client", "tool_log"}),
    "core.genesys_client": frozenset({"GenesysClient", "GenesysError", "safe_str"}),
    "core.lookups": frozenset(
        {
            "find_user_by_email",
            "get_all_queues",
            "get_queues_by_prefix",
            "find_queue_by_name",
            "get_queue_members",
            "find_user_queue_memberships",
            "user_is_queue_member",
        }
    ),
}
# `import core.cli` style is not allowed; `from core.cli import create_client` is.
ALLOWED_FROM_PLAIN = ALLOWED_PLAIN_MODULES  # `from datetime import datetime` etc.

BLOCKED_NAMES = frozenset(
    {
        "eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "getattr", "setattr",
        "delattr", "globals", "locals", "vars", "dir", "memoryview", "help", "exit", "quit",
        "SystemExit", "write_csv", "write_jsonl", "output_path", "os", "sys", "subprocess", "shutil", "pathlib",
        "requests", "socket", "importlib", "builtins", "ctypes", "pickle", "Path",
    }
)
# Interpreter-termination names: still blocked (never rewritten); the hint tells users/LLM the fix.
EXIT_NAMES = frozenset({"exit", "quit", "SystemExit"})
EXIT_HINT = " (use main() with `return` for early exit and print errors instead of exit/quit/sys.exit)"
# Attributes that would mutate, leak credentials, or bypass the request wrapper.
BLOCKED_ATTRS = frozenset(
    {
        "token", "api_host", "login_host", "authenticate", "write", "writelines", "write_text",
        "write_bytes", "unlink", "rmdir", "remove", "rename", "system", "popen", "run", "Popen",
        "post", "put", "patch", "delete", "environ", "getenv", "mkdir", "touch", "open",
        "read_text", "read_bytes", "log",
    }
)
POST_PATH_RE = re.compile(r"^/api/v2/(?:analytics/[A-Za-z0-9/_\-]*query|[A-Za-z0-9/_\-]*/search)$")
GET_PATH_RE = re.compile(r"^/api/v2/[A-Za-z0-9/_\-.{}]*$")
ALLOWED_DUNDER_NAMES = frozenset({"__name__"})


@dataclass
class SafetyResult:
    ok: bool
    violations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"passed": self.ok, "violations": self.violations}


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _joined_prefix(node: ast.AST) -> str | None:
    """Static leading text of an f-string path (e.g. f"/api/v2/users/{uid}/queues")."""
    if isinstance(node, ast.JoinedStr):
        out = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out += part.value
            else:
                out += "{}"
        return out
    return None


def check_script(code: str) -> SafetyResult:
    violations: list[str] = []

    def bad(node: ast.AST | None, message: str) -> None:
        line = getattr(node, "lineno", None)
        violations.append(f"line {line}: {message}" if line else message)

    if not isinstance(code, str) or not code.strip():
        return SafetyResult(False, ["empty script"])
    if len(code) > MAX_SCRIPT_CHARS:
        return SafetyResult(False, [f"script too long (>{MAX_SCRIPT_CHARS} chars)"])
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return SafetyResult(False, [f"syntax error: {exc.msg} (line {exc.lineno})"])

    uses_client_factory = False
    validated_request_funcs: set[int] = set()

    for node in ast.walk(tree):
        # ---- imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_PLAIN_MODULES or "." in alias.name:
                    bad(node, f"import '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                bad(node, "relative imports are not allowed")
            elif module in ALLOWED_FROM_IMPORTS:
                for alias in node.names:
                    if alias.name not in ALLOWED_FROM_IMPORTS[module]:
                        bad(node, f"'{alias.name}' from {module} is not allowed")
                    elif alias.name == "create_client":
                        uses_client_factory = True
            elif module in ALLOWED_FROM_PLAIN:
                if any(alias.name == "*" for alias in node.names):
                    bad(node, "star imports are not allowed")
            else:
                bad(node, f"import from '{module}' is not allowed")

        # ---- names / attributes
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                hint = EXIT_HINT if node.id in EXIT_NAMES else ""
                bad(node, f"use of '{node.id}' is not allowed{hint}")
            elif node.id.startswith("__") and node.id not in ALLOWED_DUNDER_NAMES:
                bad(node, f"use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                bad(node, f"dunder attribute '{node.attr}' is not allowed")
            elif node.attr in BLOCKED_ATTRS:
                bad(node, f"attribute '.{node.attr}' is not allowed")

        # ---- string tricks (format-string attribute traversal, dunder smuggling)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "__" in node.value and node.value != "__main__":
                bad(node, "string containing '__' is not allowed")

        # ---- other forbidden constructs
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bad(node, "global/nonlocal is not allowed")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            bad(node, "async code is not allowed")
        elif isinstance(node, ast.Delete):
            bad(node, "del is not allowed")

        # ---- client.request(...) validation
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "request":
            validated_request_funcs.add(id(node.func))
            args = list(node.args)
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            if any(kw.arg is None for kw in node.keywords) or any(isinstance(a, ast.Starred) for a in args):
                bad(node, "client.request must not use *args/**kwargs expansion")
                continue
            method_node = args[0] if args else kwargs.get("method")
            path_node = args[1] if len(args) > 1 else kwargs.get("path")
            method = _const_str(method_node) if method_node is not None else None
            if method is None:
                bad(node, "client.request method must be a string literal")
                continue
            method = method.upper()
            path = _const_str(path_node) if path_node is not None else None
            if path is None:
                path = _joined_prefix(path_node) if path_node is not None else None
            if path is None:
                bad(node, "client.request path must be a string literal or f-string")
                continue
            if method == "GET":
                if not path.startswith("/api/v2/"):
                    bad(node, f"GET path must start with /api/v2/: {path!r}")
            elif method == "POST":
                if "{}" in path or not POST_PATH_RE.match(path):
                    bad(node, f"POST only allowed to read-only analytics query/search endpoints, not {path!r}")
            else:
                bad(node, f"HTTP method {method} is blocked (read-only: GET or analytics/search POST)")

    # `.request` referenced but not directly called (aliasing) -> block
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "request" and id(node) not in validated_request_funcs:
            bad(node, "client.request may only be called directly (no aliasing)")

    if not uses_client_factory:
        violations.append("script must import and use create_client from core.cli")

    # Deduplicate, preserve order
    seen: set[str] = set()
    unique = [v for v in violations if not (v in seen or seen.add(v))]
    return SafetyResult(not unique, unique)
