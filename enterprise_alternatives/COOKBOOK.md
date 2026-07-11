# Cookbook

Copy-paste recipes for every custom feature in this fork. Each recipe is
self-contained: prerequisites → steps → verify.

- [0. One-time setup](#0-one-time-setup)
- [1. Traditional Chinese UI](#1-traditional-chinese-ui)
- [2. Request-plane audit log](#2-request-plane-audit-log)
- [3. Management-plane audit log](#3-management-plane-audit-log)
- [4. JWT / OAuth2 API authentication](#4-jwt--oauth2-api-authentication)
- [5. SSO group → role mapping](#5-sso-group--role-mapping)
- [6. Run the self-check](#6-run-the-self-check)
- [Troubleshooting](#troubleshooting)

---

## 0. One-time setup

The extensions must be importable as the package `enterprise_alternatives`. Run
the proxy from the repository root (the folder containing `enterprise_alternatives/`)
so that `import enterprise_alternatives...` resolves.

```bash
cd /path/to/litellm            # repo root
export PYTHONPATH="$PWD:$PYTHONPATH"
```

In Docker, mount the folder into the image working dir and add it to `PYTHONPATH`:

```yaml
# docker-compose.yml (litellm service)
volumes:
  - ./enterprise_alternatives:/app/enterprise_alternatives:ro
environment:
  PYTHONPATH: /app
```

Minimum runtime deps are already part of `litellm[proxy]` (PyJWT, cryptography,
fastapi_sso). For the optional Postgres audit sink also install `asyncpg`.

---

## 1. Traditional Chinese UI

### Use it
Open the dashboard → navbar language selector → **繁體中文**. Done — English is
still the default for everyone else.

### Add or fix one string
1. Find the exact English source string as it renders in the UI.
2. Edit `ui/litellm-dashboard/src/i18n/translations.ts`, add an entry to the
   `zhTW` object (key = English source, value = 繁體中文):
   ```ts
   const zhTW: Record<string, string> = {
     // ...
     "Delete Key": "刪除金鑰",
   };
   ```
   Keep technical tokens in English (`Token`, `API`, `MCP`, `TPM`…) and add a
   space between CJK and Latin/number tokens, e.g. `每分鐘 Token 限制 (TPM)`.
3. Rebuild the dashboard:
   ```bash
   cd ui/litellm-dashboard
   npm run build
   ```

### Verify
```bash
cd ui/litellm-dashboard
npx vitest run src/i18n        # i18n unit tests
```
The DOM translator swaps only exact matches, so an added key takes effect for
that string everywhere it renders.

---

## 2. Request-plane audit log

One audit record per LLM request: user, key, team, model, tokens, cost, status,
client IP.

### Enable
`config.yaml`:
```yaml
litellm_settings:
  callbacks: enterprise_alternatives.audit_logger.audit_logger_instance
```

Env (all optional):
```bash
export AUDIT_LOG_PATH=/var/log/litellm/audit.jsonl   # default ./litellm_audit.jsonl
export AUDIT_LOG_TO_DB=true                           # also write Postgres (needs asyncpg + DATABASE_URL)
```

Start the proxy as usual:
```bash
litellm --config config.yaml
```

### Verify
Make any completion call, then:
```bash
tail -n1 /var/log/litellm/audit.jsonl | python -m json.tool
```
You should see a `"record_type": "llm_request"` row with `user_id`, `total_tokens`,
`response_cost`, `status`, `requester_ip`.

### Query (Postgres sink)
```sql
-- spend per user, last 24h
SELECT payload->>'user_id' AS user_id,
       ROUND(SUM((payload->>'response_cost')::numeric), 4) AS spend
FROM litellm_audit_log
WHERE record_type='llm_request'
GROUP BY 1 ORDER BY spend DESC;
```

---

## 3. Management-plane audit log

Trail of key/team/model CRUD (create/update/delete/block/rotate): actor, action,
object id, HTTP status, client IP, secret-redacted body.

> The built-in management audit is gated behind `premium_user`, so this uses a
> composed ASGI app instead. You run `uvicorn` against it in place of the
> `litellm` CLI. Nothing in core is edited.

### Enable
```bash
cd /path/to/litellm
export PYTHONPATH="$PWD:$PYTHONPATH"

export CONFIG_FILE_PATH=$PWD/config.yaml     # proxy loads config from this env
export AUDIT_LOG_PATH=/var/log/litellm/audit.jsonl
export AUDIT_CAPTURE_BODY=true               # false = metadata only, no body
# (keep the request-plane callback from recipe 2 in config.yaml too, if you want both)

uvicorn enterprise_alternatives.audit_asgi_app:app --host 0.0.0.0 --port 4000
```

In `docker-compose.yml`, replace the litellm `command:` with the uvicorn line and
set `CONFIG_FILE_PATH` / `AUDIT_LOG_PATH` in `environment:`.

### Verify
Create a key through the UI or API:
```bash
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id":"team-7","models":["gpt-4"]}' >/dev/null

grep management_audit /var/log/litellm/audit.jsonl | tail -n1 | python -m json.tool
```
Expect `"action": "created"`, `"table_name": "LiteLLM_VerificationToken"`,
`"object_id": "team-7"`, `"status_code": 200`, and any `api_key`/`secret` fields
shown as `***redacted***`.

### Query
```sql
SELECT payload->>'logged_at', payload->>'changed_by', payload->>'action',
       payload->>'table_name', payload->>'object_id', payload->>'status_code'
FROM litellm_audit_log
WHERE record_type='management_audit'
ORDER BY id DESC LIMIT 50;
```

### Notes
- LLM and streaming routes are passed through untouched; only mutating requests
  to management routes are recorded.
- This does **not** fill the UI *Audit Logs* page (that reads the gated
  `LiteLLM_AuditLog` table). Use the JSONL/table above.

---

## 4. JWT / OAuth2 API authentication

Let clients call the proxy with `Authorization: Bearer <jwt>`; claims map to an
authenticated LiteLLM user.

### Enable
`config.yaml`:
```yaml
general_settings:
  custom_auth: enterprise_alternatives.custom_jwt_auth.user_api_key_auth
```

**Option A — shared secret (HS256):**
```bash
export JWT_AUTH_HS256_SECRET="use-a-random-string-at-least-32-bytes-long"
```

**Option B — OIDC provider (RS256/ES256 via JWKS):**
```bash
export JWT_AUTH_JWKS_URL="https://your-idp/.well-known/jwks.json"
export JWT_AUTH_ISSUER="https://your-idp/"
export JWT_AUTH_AUDIENCE="litellm-proxy"
```

Optional claim remaps (defaults shown):
```bash
export JWT_CLAIM_USER_ID=sub
export JWT_CLAIM_USER_EMAIL=email
export JWT_CLAIM_TEAM_ID=team_id
export JWT_CLAIM_ROLE=role          # "proxy_admin"/"proxy_admin_viewer" else internal_user
export JWT_CLAIM_MODELS=models      # list or CSV of allowed models
```

### Verify (HS256)
```bash
python - <<'PY'
import jwt, datetime
tok = jwt.encode({
    "sub":"user-42","email":"u42@example.com","team_id":"team-9",
    "role":"internal_user","models":["gpt-4"],
    "exp": datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1),
}, "use-a-random-string-at-least-32-bytes-long", algorithm="HS256")
print(tok)
PY
# then:
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer <token-from-above>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"hi"}]}'
```
A valid token authenticates; expired / tampered / wrong-issuer / missing-`sub`
tokens return HTTP 401. Use a secret ≥ 32 bytes in production.

---

## 5. SSO group → role mapping

After LiteLLM authenticates a user against your IdP, map their OIDC groups to a
LiteLLM role. Highest-privilege matching group wins.

### Prerequisites
Built-in SSO must be configured (Google/Microsoft/generic OIDC via env, e.g.
`GENERIC_CLIENT_ID` etc.). This recipe only decides the *role*, and only within
the free-tier 5-SSO-user limit.

### Enable
```bash
# surface the group claim from the IdP into the SSO user object
export GENERIC_USER_EXTRA_ATTRIBUTES="groups"
export SSO_GROUP_CLAIM="groups"

export SSO_GROUP_ROLE_MAP='{"litellm-admins":"proxy_admin","litellm-viewers":"proxy_admin_viewer"}'
export SSO_DEFAULT_ROLE="internal_user"
export SSO_DEFAULT_MAX_BUDGET="25"          # optional
export SSO_DEFAULT_BUDGET_DURATION="30d"    # optional
```

Point the proxy's custom-SSO hook at the handler:
```python
from enterprise_alternatives.custom_sso_claim_mapping import custom_sso_handler
```
(wire it where your deployment sets `user_custom_ui_sso_sign_in_handler`).

### Verify
Log in via SSO with a user in `litellm-admins` → they land as **Proxy Admin**.
A user with no mapped group → `SSO_DEFAULT_ROLE`. Precedence: admin beats viewer
beats internal. The isolated logic is covered by the self-check
(`sso:` checks in recipe 6).

---

## 6. Run the self-check

```bash
cd /path/to/litellm
python enterprise_alternatives/test_extensions.py
# or
python -m pytest enterprise_alternatives/test_extensions.py -q
```
Expected tail: `ALL CHECKS PASSED` (**53/53**). It verifies signatures against the
LiteLLM base classes and exercises JWT accept/reject, SSO precedence, audit record
shape, and the middleware (LLM pass-through + management capture + redaction).

If your interpreter is Python 3.14+, LiteLLM requires `<3.14`; use a 3.10–3.13
venv:
```bash
py -3.12 -m venv .venv_test
.venv_test/Scripts/python -m pip install -e ".[proxy]"   # Windows
# .venv_test/bin/python -m pip install -e ".[proxy]"     # Linux/mac
.venv_test/Scripts/python enterprise_alternatives/test_extensions.py
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: enterprise_alternatives` | Run from repo root; `export PYTHONPATH="$PWD:$PYTHONPATH"` (or mount + set `PYTHONPATH=/app` in Docker). |
| Audit file empty | Wrong `AUDIT_LOG_PATH`, or callback not registered (recipe 2) / not running via `audit_asgi_app` (recipe 3). |
| Management audit missing | You started with the `litellm` CLI, not `uvicorn enterprise_alternatives.audit_asgi_app:app`. |
| UI *Audit Logs* page still empty | Expected — that page reads the license-gated table. Query the JSONL/`litellm_audit_log` instead. |
| `InsecureKeyLengthWarning` | HS256 secret < 32 bytes. Use a longer secret. |
| JWKS `Unable to find a signing key` | `JWT_AUTH_JWKS_URL` wrong, or token `kid` not in the set. Confirm the `jwks_uri` from the IdP's `.well-known/openid-configuration`. |
| SSO users capped at 5 | Licensing boundary, not a bug. Set `LITELLM_LICENSE` to lift it. |
