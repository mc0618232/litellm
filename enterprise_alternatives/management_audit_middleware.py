"""
Management-plane audit via a pure-ASGI middleware (open-source, no license gate).

LiteLLM's built-in management audit (key/team/model CRUD -> LiteLLM_AuditLog table
-> UI "Audit Logs" page) is gated behind ``premium_user``
(litellm/proxy/management_helpers/audit_logs.py:208), so on the open-source proxy
it never fires and the UI page stays empty.

This middleware reconstructs an equivalent management-audit trail *without touching
that gate*. It observes mutating HTTP requests to management routes at the ASGI
layer and records who did what, to which object, with what status. Records land in
the same sink as ``audit_logger`` (JSONL file + optional Postgres table), so both
request-plane and management-plane audit live together and are queryable.

Design constraints:
* Pure ASGI (not Starlette BaseHTTPMiddleware) so it can no-op instantly for
  non-management traffic — LLM and streaming routes are passed through untouched,
  their receive/send channels never wrapped.
* Request body is tee'd ONLY for matched management mutations (small JSON, never
  streaming) using a replaying ``receive`` so the downstream handler still reads
  the full body.
* Any failure in the audit path is swallowed; it can never break a request.

Wire it via the composed ASGI app in ``audit_asgi_app.py`` (see README).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Reuse the shared sink so every audit record lands in one place.
from enterprise_alternatives.audit_logger import audit_logger_instance

# Mutating methods worth auditing.
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Management route prefixes (after any server root_path is stripped). A request is
# audited only if its path starts with one of these AND uses a mutating method.
_MANAGEMENT_PREFIXES: Tuple[str, ...] = (
    "/key/",
    "/team/",
    "/user/",
    "/organization/",
    "/customer/",
    "/end_user/",
    "/model/",
    "/model_group/",
    "/budget/",
    "/guardrails/",
    "/credentials/",
    "/mcp/",
    "/vector_store/",
    "/config/",
    "/settings/",
    "/sso/",
)
# Exact-path management routes that don't end in a trailing slash prefix above.
_MANAGEMENT_EXACT: Tuple[str, ...] = (
    "/key/generate",
    "/key/update",
    "/key/delete",
    "/key/regenerate",
    "/key/block",
    "/key/unblock",
    "/team/new",
    "/team/update",
    "/team/delete",
    "/team/member_add",
    "/team/member_delete",
    "/user/new",
    "/user/update",
    "/user/delete",
    "/organization/new",
    "/organization/update",
    "/organization/delete",
    "/model/new",
    "/model/update",
    "/model/delete",
)

# path suffix -> canonical action verb
_ACTION_SUFFIX: List[Tuple[str, str]] = [
    ("/delete", "deleted"),
    ("/block", "blocked"),
    ("/unblock", "unblocked"),
    ("/regenerate", "rotated"),
    ("/update", "updated"),
    ("/member_add", "member_added"),
    ("/member_delete", "member_removed"),
    ("/new", "created"),
    ("/generate", "created"),
]

# Common id fields to lift out of the request/response body for object_id.
_ID_FIELDS = (
    "key",
    "token",
    "key_name",
    "team_id",
    "user_id",
    "organization_id",
    "model_id",
    "id",
    "budget_id",
    "guardrail_id",
    "customer_id",
    "end_user_id",
)

# Redact obvious secrets from any captured body.
_SECRET_KEY_RE = re.compile(
    r"(api_key|secret|password|token|authorization|client_secret)", re.IGNORECASE
)

_MAX_BODY_BYTES = 64 * 1024  # never buffer more than 64 KiB for audit


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_root_path(path: str, root_path: str) -> str:
    if root_path and path.startswith(root_path):
        stripped = path[len(root_path) :]
        return stripped if stripped.startswith("/") else "/" + stripped
    return path


def _is_management_mutation(method: str, path: str) -> bool:
    if method not in _MUTATING_METHODS:
        return False
    if path in _MANAGEMENT_EXACT:
        return True
    return any(path.startswith(p) for p in _MANAGEMENT_PREFIXES)


def _derive_action(method: str, path: str) -> str:
    for suffix, verb in _ACTION_SUFFIX:
        if path.endswith(suffix):
            return verb
    return {
        "POST": "created",
        "PUT": "updated",
        "PATCH": "updated",
        "DELETE": "deleted",
    }.get(method, "changed")


def _derive_table_name(path: str) -> str:
    seg = path.strip("/").split("/", 1)[0] if path.strip("/") else ""
    return {
        "key": "LiteLLM_VerificationToken",
        "team": "LiteLLM_TeamTable",
        "user": "LiteLLM_UserTable",
        "organization": "LiteLLM_OrganizationTable",
        "model": "LiteLLM_ProxyModelTable",
        "budget": "LiteLLM_BudgetTable",
        "customer": "LiteLLM_EndUserTable",
        "end_user": "LiteLLM_EndUserTable",
    }.get(seg, seg or "unknown")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_KEY_RE.search(str(k)):
                out[k] = "***redacted***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _extract_object_id(body: Optional[dict], path: str) -> Optional[str]:
    if isinstance(body, dict):
        for f in _ID_FIELDS:
            val = body.get(f)
            if isinstance(val, str) and val:
                return val
    # fall back to a trailing path id, e.g. DELETE /model/{id}
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    if tail and tail not in {seg.strip("/") for seg in _MANAGEMENT_PREFIXES}:
        # avoid returning the resource name itself (e.g. "new", "delete")
        if tail not in {"new", "delete", "update", "generate", "block", "unblock", "regenerate"}:
            return tail
    return None


def _principal_from_scope(scope) -> Dict[str, Optional[str]]:
    """Best-effort actor identity from the auth-seam Principal on scope state."""
    state = scope.get("state") or {}
    principal = state.get("principal")
    changed_by: Optional[str] = None
    changed_by_api_key: Optional[str] = None
    try:
        if principal is not None:
            user = getattr(principal, "user", None)
            if user is not None:
                changed_by = getattr(user, "user_id", None) or getattr(user, "user_email", None)
            if not changed_by:
                changed_by = getattr(principal, "subject", None)
            cred = getattr(principal, "credential_ref", None)
            if cred is not None:
                changed_by_api_key = getattr(cred, "token_id", None)
    except Exception:
        pass
    return {"changed_by": changed_by, "changed_by_api_key": changed_by_api_key}


class ManagementAuditMiddleware:
    """Pure-ASGI middleware recording management-plane mutations."""

    def __init__(self, app, root_path: str = "", capture_body: bool = True) -> None:
        self.app = app
        self.root_path = root_path.rstrip("/")
        self.capture_body = capture_body

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "")
        raw_path = scope.get("path", "")
        path = _strip_root_path(raw_path, self.root_path)

        if not _is_management_mutation(method, path):
            # Fast path: untouched for LLM/streaming/read traffic.
            return await self.app(scope, receive, send)

        # ---- matched a management mutation: tee body + capture status ---- #
        body_parts: List[bytes] = []
        total = 0
        capturing = self.capture_body

        async def recv():
            nonlocal total, capturing
            message = await receive()
            if capturing and message.get("type") == "http.request":
                chunk = message.get("body", b"") or b""
                total += len(chunk)
                if total <= _MAX_BODY_BYTES:
                    body_parts.append(chunk)
                else:
                    capturing = False  # oversized: stop buffering, keep passing through
                    body_parts.clear()
            return message

        status_holder: Dict[str, int] = {}

        async def snd(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = message.get("status", 0)
            await send(message)

        # Run the app; downstream still reads the full body via ``recv``.
        await self.app(scope, recv, snd)

        # ---- emit audit record (never let this break the response) ---- #
        try:
            await self._emit(scope, method, path, body_parts, status_holder.get("status"))
        except Exception as exc:  # noqa: BLE001
            print(f"[management_audit] emit failed: {exc}")  # noqa: T201

    async def _emit(self, scope, method, path, body_parts, status) -> None:
        parsed_body: Optional[dict] = None
        if body_parts:
            raw = b"".join(body_parts)
            if raw:
                try:
                    loaded = json.loads(raw)
                    parsed_body = loaded if isinstance(loaded, dict) else {"_body": loaded}
                except Exception:
                    parsed_body = None

        actor = _principal_from_scope(scope)
        client = scope.get("client") or (None, None)
        query = scope.get("query_string", b"").decode("latin-1") or None
        success = status is not None and 200 <= status < 300

        record = {
            "record_type": "management_audit",
            "logged_at": _utcnow_iso(),
            "source": "middleware",
            "action": _derive_action(method, path),
            "table_name": _derive_table_name(path),
            "object_id": _extract_object_id(parsed_body, path),
            "method": method,
            "route": path,
            "query": query,
            "status_code": status,
            "success": success,
            "changed_by": actor["changed_by"],
            "changed_by_api_key": actor["changed_by_api_key"],
            "client_ip": client[0] if client else None,
            "updated_values": json.dumps(_redact(parsed_body), ensure_ascii=False)
            if parsed_body is not None
            else None,
        }
        await audit_logger_instance.emit(record)
