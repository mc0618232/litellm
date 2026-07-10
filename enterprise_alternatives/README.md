# Self-built enterprise-alternative extensions

Open-source implementations of three capabilities that LiteLLM otherwise gates
behind an Enterprise license, built entirely on **public extension points** — no
license code is patched or bypassed. You own this code; it runs on the free
open-source proxy.

| Module | Replaces | Extension point |
|---|---|---|
| `audit_logger.py` | Enterprise Audit Logs | `litellm_settings.callbacks` (CustomLogger) |
| `custom_jwt_auth.py` | Enterprise JWT/OAuth API auth | `general_settings.custom_auth` |
| `custom_sso_claim_mapping.py` | SSO role/group mapping | `custom_ui_sso_sign_in_handler` |

> **Not included on purpose:** raising the free-tier "5 SSO users" limit. That is a
> licensing boundary, not a technical one — it is checked before any of these
> handlers run. To exceed it legitimately set `LITELLM_LICENSE`
> (https://enterprise.litellm.ai/demo).

## Self-check

```bash
python enterprise_alternatives/test_extensions.py
# or
python -m pytest enterprise_alternatives/test_extensions.py -q
```

The suite verifies every entrypoint matches the LiteLLM base signature and
exercises JWT accept/reject paths, SSO group→role precedence, and audit record
shape. Last run: **37/37 checks passed**.

---

## 1. Audit logging

Captures one record per LLM request (user, key, team, model, tokens, cost,
status, client IP) plus every management action (key/team/model create, update,
delete, block, rotate). Records go to a JSONL file, and optionally a dedicated
Postgres table that never touches the Prisma schema.

```yaml
# config.yaml
litellm_settings:
  callbacks: enterprise_alternatives.audit_logger.audit_logger_instance
```

Env:

| Var | Default | Meaning |
|---|---|---|
| `AUDIT_LOG_PATH` | `litellm_audit.jsonl` | JSONL output path |
| `AUDIT_LOG_TO_DB` | `false` | also insert into `litellm_audit_log` table |
| `DATABASE_URL` | — | used when `AUDIT_LOG_TO_DB=true` (needs `asyncpg`) |

The table is auto-created on first write:

```sql
CREATE TABLE litellm_audit_log (
  id BIGSERIAL PRIMARY KEY,
  logged_at TEXT NOT NULL,
  record_type TEXT NOT NULL,   -- 'llm_request' | 'management_audit'
  payload JSONB NOT NULL
);
```

Query examples:

```sql
-- spend per user, last 24h
SELECT payload->>'user_id' AS user_id, SUM((payload->>'response_cost')::float)
FROM litellm_audit_log WHERE record_type='llm_request'
GROUP BY 1 ORDER BY 2 DESC;

-- who deleted keys
SELECT payload->>'changed_by', payload->>'object_id', payload->>'action'
FROM litellm_audit_log
WHERE record_type='management_audit' AND payload->>'table_name' LIKE '%VerificationToken%';
```

Failures in the audit sink are swallowed and printed — they never break a request.

---

## 2. JWT / OAuth2 API authentication

Validates `Authorization: Bearer <jwt>` locally and maps claims to an
authenticated user. Two modes:

```yaml
# config.yaml
general_settings:
  custom_auth: enterprise_alternatives.custom_jwt_auth.user_api_key_auth
```

**HS256 (shared secret):**

```bash
export JWT_AUTH_HS256_SECRET="a-32-byte-or-longer-random-secret-string"
```

**RS256/ES256 (OIDC provider JWKS):**

```bash
export JWT_AUTH_JWKS_URL="https://your-idp/.well-known/jwks.json"
export JWT_AUTH_ISSUER="https://your-idp/"      # optional but recommended
export JWT_AUTH_AUDIENCE="litellm-proxy"        # optional but recommended
```

Claim mapping (override names if your IdP differs):

| Env | Default claim | Maps to |
|---|---|---|
| `JWT_CLAIM_USER_ID` | `sub` | `user_id` (required) |
| `JWT_CLAIM_USER_EMAIL` | `email` | `user_email` |
| `JWT_CLAIM_TEAM_ID` | `team_id` | `team_id` |
| `JWT_CLAIM_ROLE` | `role` | `user_role` (`proxy_admin`/`proxy_admin_viewer` else `internal_user`) |
| `JWT_CLAIM_MODELS` | `models` | allowed models (list or CSV) |

`exp` is always required; expired, tampered, wrong-issuer/audience, and
missing-`sub` tokens are rejected with HTTP 401. Use a secret ≥ 32 bytes in
production (shorter keys trigger a PyJWT `InsecureKeyLengthWarning`).

---

## 3. SSO claim → role mapping

After LiteLLM authenticates the user against your IdP, maps OIDC group claims to
a LiteLLM role. Highest-privilege matching group wins.

```bash
# capture the group claim from the IdP
export GENERIC_USER_EXTRA_ATTRIBUTES="groups"
export SSO_GROUP_CLAIM="groups"

export SSO_GROUP_ROLE_MAP='{"litellm-admins":"proxy_admin","litellm-viewers":"proxy_admin_viewer"}'
export SSO_DEFAULT_ROLE="internal_user"
export SSO_DEFAULT_MAX_BUDGET="25"          # optional
export SSO_DEFAULT_BUDGET_DURATION="30d"    # optional
```

Point the proxy at the handler (in code where the proxy is initialised, or via
your existing custom-SSO wiring):

```python
from enterprise_alternatives.custom_sso_claim_mapping import custom_sso_handler
```

Again: this decides *which role* an allowed user gets. It does not change *how
many* SSO users may sign in.
```
