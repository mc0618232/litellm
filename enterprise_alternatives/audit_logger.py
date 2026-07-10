"""
Self-built audit logging for LiteLLM proxy (open-source, no Enterprise license needed).

Wires into two public extension points on ``litellm.integrations.custom_logger.CustomLogger``:

* ``async_log_success_event`` / ``async_log_failure_event`` — one audit record per
  LLM request (who, which key/team/user, model, tokens, cost, status, client IP).
* ``async_log_audit_log_event`` — management-plane audit (key/team/model create,
  update, delete, block, rotate) using LiteLLM's ``StandardAuditLogPayload``.

Records are appended as JSON lines to ``AUDIT_LOG_PATH`` (default
``./litellm_audit.jsonl``). If ``AUDIT_LOG_TO_DB=true`` and ``asyncpg`` plus
``DATABASE_URL`` are available, records are also inserted into a dedicated
``litellm_audit_log`` table (created on first use — it never touches the Prisma
schema, so migrations are unaffected).

Enable in config.yaml:

    litellm_settings:
      callbacks: enterprise_alternatives.audit_logger.audit_logger_instance
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from litellm.integrations.custom_logger import CustomLogger


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AuditLogger(CustomLogger):
    """Append-only audit sink for request-plane and management-plane events."""

    def __init__(self) -> None:
        super().__init__()
        self.log_path = os.getenv("AUDIT_LOG_PATH", "litellm_audit.jsonl")
        self.log_to_db = os.getenv("AUDIT_LOG_TO_DB", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self.database_url = os.getenv("DATABASE_URL")
        # Serialize file writes so concurrent requests don't interleave lines.
        self._file_lock = asyncio.Lock()
        self._db_ready = False
        self._db_lock = asyncio.Lock()
        self._db_disabled = not (self.log_to_db and self.database_url)

    # ------------------------------------------------------------------ #
    # Request-plane audit
    # ------------------------------------------------------------------ #
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        await self._emit(self._build_request_record(kwargs, "success", start_time, end_time))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        await self._emit(self._build_request_record(kwargs, "failure", start_time, end_time))

    def _build_request_record(self, kwargs: Dict, status: str, start_time, end_time) -> Dict[str, Any]:
        standard = kwargs.get("standard_logging_object") or {}
        metadata = standard.get("metadata") or {}

        def dur() -> Optional[float]:
            try:
                return (end_time - start_time).total_seconds()
            except Exception:
                return standard.get("response_time")

        return {
            "record_type": "llm_request",
            "logged_at": _utcnow_iso(),
            "request_id": standard.get("id") or kwargs.get("litellm_call_id"),
            "trace_id": standard.get("trace_id"),
            "status": standard.get("status") or status,
            "call_type": standard.get("call_type") or kwargs.get("call_type"),
            "model": standard.get("model") or kwargs.get("model"),
            "model_group": standard.get("model_group"),
            "custom_llm_provider": standard.get("custom_llm_provider"),
            "api_key_hash": metadata.get("user_api_key_hash"),
            "api_key_alias": metadata.get("user_api_key_alias"),
            "user_id": metadata.get("user_api_key_user_id"),
            "user_email": metadata.get("user_api_key_user_email"),
            "team_id": metadata.get("user_api_key_team_id"),
            "team_alias": metadata.get("user_api_key_team_alias"),
            "org_id": metadata.get("user_api_key_org_id"),
            "end_user": standard.get("end_user"),
            "requester_ip": standard.get("requester_ip_address")
            or metadata.get("requester_ip_address"),
            "user_agent": standard.get("user_agent") or metadata.get("user_agent"),
            "prompt_tokens": standard.get("prompt_tokens"),
            "completion_tokens": standard.get("completion_tokens"),
            "total_tokens": standard.get("total_tokens"),
            "response_cost": _as_float(standard.get("response_cost")),
            "response_time_s": dur(),
            "request_tags": standard.get("request_tags") or [],
            "applied_guardrails": metadata.get("applied_guardrails") or [],
            "error": standard.get("error_str"),
        }

    # ------------------------------------------------------------------ #
    # Management-plane audit (key/team/model CRUD, block, rotate)
    # ------------------------------------------------------------------ #
    async def async_log_audit_log_event(self, audit_log):
        record = {
            "record_type": "management_audit",
            "logged_at": _utcnow_iso(),
            "audit_id": audit_log.get("id"),
            "updated_at": audit_log.get("updated_at"),
            "changed_by": audit_log.get("changed_by"),
            "changed_by_api_key": audit_log.get("changed_by_api_key"),
            "action": audit_log.get("action"),
            "table_name": audit_log.get("table_name"),
            "object_id": audit_log.get("object_id"),
            "before_value": audit_log.get("before_value"),
            "updated_values": audit_log.get("updated_values"),
        }
        await self._emit(record)

    # ------------------------------------------------------------------ #
    # Sinks
    # ------------------------------------------------------------------ #
    async def _emit(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        await self._write_file(line)
        if not self._db_disabled:
            await self._write_db(record, line)

    async def _write_file(self, line: str) -> None:
        async with self._file_lock:
            try:
                # Synchronous append under the lock: atomic per line, no partial writes.
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception as exc:  # never let audit logging break a request
                print(f"[audit_logger] file write failed: {exc}")  # noqa: T201

    async def _write_db(self, record: Dict[str, Any], line: str) -> None:
        try:
            import asyncpg  # type: ignore
        except ImportError:
            # asyncpg absent -> silently fall back to file-only for the process lifetime.
            self._db_disabled = True
            return

        try:
            await self._ensure_db(asyncpg)
            conn = await asyncpg.connect(self.database_url)
            try:
                await conn.execute(
                    """
                    INSERT INTO litellm_audit_log (logged_at, record_type, payload)
                    VALUES ($1, $2, $3::jsonb)
                    """,
                    record.get("logged_at"),
                    record.get("record_type"),
                    line,
                )
            finally:
                await conn.close()
        except Exception as exc:  # DB problems must not break the request path
            print(f"[audit_logger] db write failed: {exc}")  # noqa: T201

    async def _ensure_db(self, asyncpg) -> None:
        if self._db_ready:
            return
        async with self._db_lock:
            if self._db_ready:
                return
            conn = await asyncpg.connect(self.database_url)
            try:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS litellm_audit_log (
                        id BIGSERIAL PRIMARY KEY,
                        logged_at TEXT NOT NULL,
                        record_type TEXT NOT NULL,
                        payload JSONB NOT NULL
                    )
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS litellm_audit_log_type_idx "
                    "ON litellm_audit_log (record_type)"
                )
            finally:
                await conn.close()
            self._db_ready = True


# Instance referenced from config.yaml callbacks.
audit_logger_instance = AuditLogger()
