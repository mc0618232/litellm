"""
Composed ASGI entrypoint: LiteLLM proxy + management-audit middleware.

This does NOT modify any LiteLLM core file. It imports the proxy's FastAPI app and
wraps it with ``ManagementAuditMiddleware`` at import time (middleware must be added
before the lifespan runs — the proxy freezes its middleware stack once startup
begins). Config is still loaded from ``CONFIG_FILE_PATH`` exactly as with the
normal ``litellm`` CLI.

Run instead of ``litellm --config``:

    export CONFIG_FILE_PATH=/app/config.yaml
    export AUDIT_LOG_PATH=/var/log/litellm/audit.jsonl
    uvicorn enterprise_alternatives.audit_asgi_app:app --host 0.0.0.0 --port 4000

All existing proxy routes/behaviour are unchanged; management mutations are now
recorded to the audit sink even on the open-source proxy.
"""

from __future__ import annotations

import os

# Importing the proxy module creates ``app`` and loads server-root-path config.
from litellm.proxy.proxy_server import app as _litellm_app

from enterprise_alternatives.management_audit_middleware import ManagementAuditMiddleware

_root_path = os.getenv("SERVER_ROOT_PATH", "") or getattr(_litellm_app, "root_path", "") or ""
_capture_body = os.getenv("AUDIT_CAPTURE_BODY", "true").strip().lower() in ("1", "true", "yes")

# Add before the app starts. FastAPI/Starlette applies middleware in reverse add
# order; this outermost wrapper sees the raw request and the final status.
_litellm_app.add_middleware(
    ManagementAuditMiddleware,
    root_path=_root_path,
    capture_body=_capture_body,
)

# The object uvicorn serves.
app = _litellm_app
