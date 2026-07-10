"""
Self-check for the self-built enterprise-alternative extensions.

Run:  python -m pytest enterprise_alternatives/test_extensions.py -q
   or: python enterprise_alternatives/test_extensions.py   (plain runner, no pytest)

Verifies:
* each module imports and its entrypoint matches the LiteLLM base signature
* JWT auth: HS256 accept / reject-expired / reject-tampered / role+model mapping
* SSO handler: group->role precedence, default fallback, budget env
* audit logger: request-plane + management-plane record shapes, JSONL sink
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import jwt  # noqa: E402

from litellm.integrations.custom_logger import CustomLogger  # noqa: E402
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# 1. Signature / contract checks
# --------------------------------------------------------------------------- #
def test_signatures():
    from enterprise_alternatives import audit_logger, custom_jwt_auth, custom_sso_claim_mapping

    check(
        "audit_logger.audit_logger_instance is a CustomLogger",
        isinstance(audit_logger.audit_logger_instance, CustomLogger),
    )
    # entrypoint override presence
    base = CustomLogger
    inst = audit_logger.audit_logger_instance
    for hook in ("async_log_success_event", "async_log_failure_event", "async_log_audit_log_event"):
        check(
            f"audit_logger overrides {hook}",
            getattr(type(inst), hook) is not getattr(base, hook),
        )

    sig = inspect.signature(custom_jwt_auth.user_api_key_auth)
    check(
        "custom_jwt_auth.user_api_key_auth(request, api_key)",
        list(sig.parameters) == ["request", "api_key"],
        detail=str(list(sig.parameters)),
    )
    check(
        "custom_jwt_auth.user_api_key_auth is async",
        inspect.iscoroutinefunction(custom_jwt_auth.user_api_key_auth),
    )

    sig2 = inspect.signature(custom_sso_claim_mapping.custom_sso_handler)
    check(
        "custom_sso_handler(userIDPInfo)",
        list(sig2.parameters) == ["userIDPInfo"],
        detail=str(list(sig2.parameters)),
    )
    check(
        "custom_sso_handler is async",
        inspect.iscoroutinefunction(custom_sso_claim_mapping.custom_sso_handler),
    )


# --------------------------------------------------------------------------- #
# 2. JWT auth behaviour
# --------------------------------------------------------------------------- #
def test_jwt_auth():
    os.environ["JWT_AUTH_HS256_SECRET"] = "test-secret-123"
    os.environ.pop("JWT_AUTH_JWKS_URL", None)
    os.environ.pop("JWT_AUTH_ISSUER", None)
    os.environ.pop("JWT_AUTH_AUDIENCE", None)
    os.environ.pop("JWT_AUTH_ALGORITHMS", None)

    # reimport fresh so module-level state is clean
    import importlib

    from enterprise_alternatives import custom_jwt_auth

    importlib.reload(custom_jwt_auth)

    secret = "test-secret-123"
    now = datetime.now(timezone.utc)

    valid = jwt.encode(
        {
            "sub": "user-42",
            "email": "u42@example.com",
            "team_id": "team-9",
            "role": "proxy_admin",
            "models": ["gpt-4", "gpt-3.5-turbo"],
            "exp": now + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )

    result = run(custom_jwt_auth.user_api_key_auth(None, f"Bearer {valid}"))
    check("jwt: valid token accepted", isinstance(result, UserAPIKeyAuth))
    check("jwt: user_id mapped", result.user_id == "user-42", detail=str(result.user_id))
    check("jwt: email mapped", result.user_email == "u42@example.com")
    check("jwt: team mapped", result.team_id == "team-9")
    check("jwt: admin role mapped", result.user_role == LitellmUserRoles.PROXY_ADMIN)
    check("jwt: models mapped", result.models == ["gpt-4", "gpt-3.5-turbo"], detail=str(result.models))
    check("jwt: claims attached", (result.jwt_claims or {}).get("sub") == "user-42")

    # expired
    expired = jwt.encode({"sub": "x", "exp": now - timedelta(hours=1)}, secret, algorithm="HS256")
    rejected = False
    try:
        run(custom_jwt_auth.user_api_key_auth(None, expired))
    except Exception:
        rejected = True
    check("jwt: expired token rejected", rejected)

    # tampered / wrong secret
    forged = jwt.encode({"sub": "x", "exp": now + timedelta(hours=1)}, "wrong-secret", algorithm="HS256")
    rejected = False
    try:
        run(custom_jwt_auth.user_api_key_auth(None, forged))
    except Exception:
        rejected = True
    check("jwt: wrong-signature token rejected", rejected)

    # missing sub
    nosub = jwt.encode({"email": "a@b.c", "exp": now + timedelta(hours=1)}, secret, algorithm="HS256")
    rejected = False
    try:
        run(custom_jwt_auth.user_api_key_auth(None, nosub))
    except Exception:
        rejected = True
    check("jwt: missing-sub token rejected", rejected)

    # default role fallback
    plain = jwt.encode({"sub": "u2", "exp": now + timedelta(hours=1)}, secret, algorithm="HS256")
    res2 = run(custom_jwt_auth.user_api_key_auth(None, plain))
    check("jwt: default role is internal_user", res2.user_role == LitellmUserRoles.INTERNAL_USER)


# --------------------------------------------------------------------------- #
# 3. SSO claim mapping
# --------------------------------------------------------------------------- #
class _FakeOpenID:
    def __init__(self, id, email, extra_fields=None):
        self.id = id
        self.email = email
        self.extra_fields = extra_fields or {}


def test_sso_mapping():
    import importlib

    os.environ["SSO_GROUP_ROLE_MAP"] = json.dumps(
        {"litellm-admins": "proxy_admin", "litellm-viewers": "proxy_admin_viewer"}
    )
    os.environ["SSO_GROUP_CLAIM"] = "groups"
    os.environ["SSO_DEFAULT_ROLE"] = "internal_user"
    os.environ["SSO_DEFAULT_MAX_BUDGET"] = "25"
    os.environ["SSO_DEFAULT_BUDGET_DURATION"] = "30d"

    from enterprise_alternatives import custom_sso_claim_mapping

    importlib.reload(custom_sso_claim_mapping)

    # admin group
    admin = _FakeOpenID("id1", "admin@example.com", {"groups": ["staff", "litellm-admins"]})
    r = run(custom_sso_claim_mapping.custom_sso_handler(admin))
    check("sso: admin group -> proxy_admin", r["user_role"] == LitellmUserRoles.PROXY_ADMIN.value, detail=str(r["user_role"]))
    check("sso: user_id passthrough", r["user_id"] == "id1")
    check("sso: budget env applied", r["max_budget"] == 25.0 and r["budget_duration"] == "30d")

    # precedence: admin beats viewer
    both = _FakeOpenID("id2", "b@e.com", {"groups": "litellm-viewers,litellm-admins"})
    r2 = run(custom_sso_claim_mapping.custom_sso_handler(both))
    check("sso: precedence admin>viewer", r2["user_role"] == LitellmUserRoles.PROXY_ADMIN.value)

    # no matching group -> default
    none = _FakeOpenID("id3", "c@e.com", {"groups": ["random"]})
    r3 = run(custom_sso_claim_mapping.custom_sso_handler(none))
    check("sso: unmatched -> default internal_user", r3["user_role"] == LitellmUserRoles.INTERNAL_USER.value)

    # viewer only
    v = _FakeOpenID("id4", "d@e.com", {"groups": ["litellm-viewers"]})
    r4 = run(custom_sso_claim_mapping.custom_sso_handler(v))
    check("sso: viewer group -> proxy_admin_viewer", r4["user_role"] == LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY.value)


# --------------------------------------------------------------------------- #
# 4. Audit logger record shape + JSONL sink
# --------------------------------------------------------------------------- #
def test_audit_logger():
    import importlib

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "audit.jsonl")
    os.environ["AUDIT_LOG_PATH"] = path
    os.environ["AUDIT_LOG_TO_DB"] = "false"

    from enterprise_alternatives import audit_logger

    importlib.reload(audit_logger)
    logger = audit_logger.AuditLogger()

    start = datetime.now(timezone.utc)
    end = start + timedelta(seconds=2)
    kwargs = {
        "litellm_call_id": "call-1",
        "model": "gpt-4",
        "standard_logging_object": {
            "id": "req-1",
            "trace_id": "trace-1",
            "status": "success",
            "call_type": "acompletion",
            "model": "gpt-4",
            "model_group": "gpt-4-group",
            "custom_llm_provider": "openai",
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "response_cost": 0.0012,
            "end_user": "eu-1",
            "requester_ip_address": "10.0.0.1",
            "user_agent": "curl/8",
            "request_tags": ["prod"],
            "metadata": {
                "user_api_key_hash": "hash-abc",
                "user_api_key_alias": "prod-key",
                "user_api_key_user_id": "user-1",
                "user_api_key_user_email": "user1@example.com",
                "user_api_key_team_id": "team-1",
                "user_api_key_team_alias": "team-one",
                "user_api_key_org_id": "org-1",
                "applied_guardrails": ["pii"],
            },
        },
    }
    run(logger.async_log_success_event(kwargs, {}, start, end))

    audit = {
        "id": "audit-1",
        "updated_at": "2026-01-01T00:00:00Z",
        "changed_by": "admin@example.com",
        "changed_by_api_key": "hash-admin",
        "action": "created",
        "table_name": "LiteLLM_VerificationToken",
        "object_id": "key-123",
        "before_value": None,
        "updated_values": json.dumps({"models": ["gpt-4"]}),
    }
    run(logger.async_log_audit_log_event(audit))

    with open(path, encoding="utf-8") as fh:
        lines = [json.loads(x) for x in fh if x.strip()]

    check("audit: two records written", len(lines) == 2, detail=str(len(lines)))
    req = next((r for r in lines if r["record_type"] == "llm_request"), None)
    mgmt = next((r for r in lines if r["record_type"] == "management_audit"), None)

    check("audit: request record present", req is not None)
    if req:
        check("audit: request captures user", req["user_id"] == "user-1")
        check("audit: request captures key hash", req["api_key_hash"] == "hash-abc")
        check("audit: request captures tokens", req["total_tokens"] == 30)
        check("audit: request captures cost", abs((req["response_cost"] or 0) - 0.0012) < 1e-9)
        check("audit: request captures ip", req["requester_ip"] == "10.0.0.1")
        check("audit: request response_time computed", req["response_time_s"] == 2.0)

    check("audit: management record present", mgmt is not None)
    if mgmt:
        check("audit: mgmt action", mgmt["action"] == "created")
        check("audit: mgmt object_id", mgmt["object_id"] == "key-123")
        check("audit: mgmt changed_by", mgmt["changed_by"] == "admin@example.com")


def main():
    test_signatures()
    test_jwt_auth()
    test_sso_mapping()
    test_audit_logger()
    print("\n" + "=" * 50)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


# pytest entrypoints
def test_all_signatures():
    test_signatures()
    assert not _failures


if __name__ == "__main__":
    main()
