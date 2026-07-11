# Custom features (this fork)

Additions layered on top of upstream LiteLLM. Everything here is built on
open-source-safe extension points or the UI layer — **no license gate is patched
or bypassed**. Where a capability is genuinely license-gated (e.g. the UI Audit
Logs page, the >5 SSO-user limit), that boundary is left intact and documented.

| Feature | What it gives you | Where |
|---|---|---|
| Traditional Chinese UI (zh-TW) | Full dashboard translation (~3,600 strings) | `ui/litellm-dashboard/src/i18n/` |
| Request-plane audit | Per-request audit log (user/key/team/model/tokens/cost/IP) | `enterprise_alternatives/audit_logger.py` |
| Management-plane audit | Key/team/model CRUD trail (actor/action/object/status) | `enterprise_alternatives/management_audit_middleware.py` |
| JWT / OAuth2 API auth | Authenticate API calls with a Bearer JWT | `enterprise_alternatives/custom_jwt_auth.py` |
| SSO claim → role mapping | Map OIDC groups to LiteLLM roles at login | `enterprise_alternatives/custom_sso_claim_mapping.py` |

- **Extensions detail & config reference:** [`enterprise_alternatives/README.md`](enterprise_alternatives/README.md)
- **Step-by-step recipes:** [`enterprise_alternatives/COOKBOOK.md`](enterprise_alternatives/COOKBOOK.md)
- **Self-check:** `python enterprise_alternatives/test_extensions.py` → **53/53 checks passing**

---

## 1. Traditional Chinese UI (zh-TW)

The admin dashboard renders fully in 繁體中文 (Taiwan usage). Translation is a
DOM-level dictionary layer: rendered text nodes and a few user-visible attributes
whose trimmed content exactly matches a dictionary key are swapped, and a
`MutationObserver` keeps newly rendered content translated. User data (key
aliases, model names, emails) is never matched, so it is never altered.

- Dictionary: `ui/litellm-dashboard/src/i18n/translations.ts` (~3,600 entries)
- DOM layer: `ui/litellm-dashboard/src/i18n/domTranslator.ts`
- Coverage: ~98% of extractable UI strings. The rest are brand/SDK names and
  code identifiers left intentionally in English.

**Use:** switch language to **繁體中文** in the navbar language selector. No env
or build flag needed; English remains the default.

See the COOKBOOK for how to add or fix an individual string.

---

## 2–5. Enterprise-alternative extensions

Four backend capabilities that upstream otherwise gates behind an Enterprise
license, re-implemented on public extension points. Full env-var reference is in
[`enterprise_alternatives/README.md`](enterprise_alternatives/README.md); runnable
end-to-end recipes are in
[`enterprise_alternatives/COOKBOOK.md`](enterprise_alternatives/COOKBOOK.md).

Quick map of *how* each hooks in (nothing in core is edited):

- **Request audit** → registered as a `CustomLogger` in `litellm_settings.callbacks`.
- **Management audit** → a composed ASGI app (`audit_asgi_app.py`) wraps the real
  proxy app with a middleware; you run `uvicorn` against it instead of the
  `litellm` CLI.
- **JWT auth** → `general_settings.custom_auth` points at the handler.
- **SSO mapping** → a `custom_sso_handler` invoked after the IdP round-trip.

### Honest limitations (by design)

- The management audit does **not** repopulate the UI *Audit Logs* page — that
  page reads the license-gated `LiteLLM_AuditLog` table through an Enterprise
  endpoint. Query the JSONL file / `litellm_audit_log` table instead.
- The SSO mapping decides *which role* a user gets; it does **not** raise the
  free-tier 5-SSO-user limit (a licensing boundary checked before the handler
  runs). To exceed it legitimately, set `LITELLM_LICENSE`
  (https://enterprise.litellm.ai/demo).
