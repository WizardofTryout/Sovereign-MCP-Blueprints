"""
=============================================================================
Sovereign MCP Blueprints — auditable-sql-mcp
server.py — MCP Server with GDPR Anonymisation & Compliance Audit Logger

Author   : Sovereign MCP Blueprints Contributors
License  : MIT
Spec     : Model Context Protocol 2024-11-05
Runtime  : Python 3.12+ (Docker only)

Philosophy
----------
This server acts as the ONLY gateway between Claude (Anthropic API) and your
enterprise PostgreSQL database. It enforces three invariants that cannot be
bypassed by Claude:

  1. AUDIT-BEFORE-EXECUTE — An immutable audit record is written BEFORE any
     database query is executed. If the write fails, the query is aborted.
     This satisfies GDPR Article 5(2) accountability requirements.

  2. READ-ONLY ENFORCEMENT — All queries are executed over a read-only
     database connection. DDL and DML statements are blocked at the
     connection level, not just by prompt engineering.

  3. ANONYMISATION LAYER — PII fields are stripped or pseudonymised before
     results are returned to Claude. Claude never sees raw personal data.

Audit Log Format (JSONL, one record per line)
---------------------------------------------
{
  "event_type": "MCP_TOOL_CALL",
  "timestamp": "<ISO-8601 UTC>",
  "session_id": "<uuid>",
  "tool_name": "<string>",
  "parameters": { ... },        // sanitised — no raw PII
  "actor": "claude",
  "compliance_tags": ["GDPR_ART_25", "READ_ONLY", "ANONYMISED"],
  "status": "SUCCESS" | "FAILURE" | "BLOCKED",
  "result_row_count": <int>,
  "latency_ms": <float>,
  "error": "<string | null>"
}
=============================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import asyncpg
import structlog
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings

# =============================================================================
# Configuration — loaded from environment variables (via docker-compose .env)
# =============================================================================

class ServerSettings(BaseSettings):
    """
    All configuration is sourced from environment variables.
    No secrets are ever hardcoded. In air-gapped environments,
    set these via Docker secrets or a vault sidecar.
    """
    database_url: str = Field(
        ...,
        description="PostgreSQL DSN. Must point to a read-only role.",
    )
    mcp_server_name: str = Field(default="sovereign-sql-mcp")
    mcp_server_version: str = Field(default="1.0.0")
    audit_log_mode: str = Field(
        default="stdout",
        description="'stdout' (container log aggregation) or 'file'",
    )
    audit_log_file: str = Field(
        default="/var/log/mcp/audit.jsonl",
        description="Path when audit_log_mode='file'",
    )
    query_max_rows: int = Field(
        default=50,
        description="Hard cap on rows returned. GDPR Art. 5(1)(c) — data minimisation.",
        ge=1,
        le=1000,
    )
    log_level: str = Field(default="INFO")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = ServerSettings()  # type: ignore[call-arg]

# =============================================================================
# Structured Logging Setup (structlog → JSON output)
# =============================================================================

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger("sovereign-mcp")

# =============================================================================
# Audit Logger — The Compliance Core
# =============================================================================

class AuditLogger:
    """
    Immutable, append-only audit logger for every MCP tool invocation.

    GDPR Compliance Notes:
    ----------------------
    - Records are written BEFORE query execution (Art. 5(2) accountability).
    - Parameter values are hashed when they contain PII-adjacent keys,
      ensuring the audit log itself does not become a PII store.
    - Output format is JSONL for direct ingestion into Splunk, Elastic, or Loki.
    - In file mode, the log file should be mounted to a WORM storage volume
      or shipped to an immutable SIEM within your retention window.

    ISO 27001 Alignment:
    --------------------
    - Control A.12.4.1: Event logging
    - Control A.12.4.3: Administrator and operator logs
    """

    # Fields that should be hashed (SHA-256) rather than logged verbatim
    # to prevent the audit log itself from becoming a PII store.
    SENSITIVE_PARAM_KEYS: frozenset[str] = frozenset({
        "name", "email", "phone", "ssn", "iban", "dob",
        "date_of_birth", "national_id", "passport",
    })

    def __init__(self) -> None:
        self._file_handle: Any = None
        if settings.audit_log_mode == "file":
            os.makedirs(os.path.dirname(settings.audit_log_file), exist_ok=True)
            self._file_handle = open(settings.audit_log_file, "a", encoding="utf-8")
            logger.info("audit_logger_initialised", mode="file", path=settings.audit_log_file)
        else:
            logger.info("audit_logger_initialised", mode="stdout")

    def _sanitise_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Replace sensitive parameter values with their SHA-256 hash.
        Preserves auditability (we know a value was provided) without
        logging raw PII into the audit store.
        """
        sanitised: dict[str, Any] = {}
        for key, value in params.items():
            if key.lower() in self.SENSITIVE_PARAM_KEYS and isinstance(value, str):
                sanitised[key] = f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
            else:
                sanitised[key] = value
        return sanitised

    def record(
        self,
        *,
        session_id: str,
        tool_name: str,
        parameters: dict[str, Any],
        status: str,                      # "SUCCESS" | "FAILURE" | "BLOCKED"
        result_row_count: int = 0,
        latency_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        """
        Write a single audit record. This method is synchronous and blocking —
        intentionally so. We must guarantee the record is committed to the
        audit sink before returning control to the caller.
        """
        record: dict[str, Any] = {
            "event_type": "MCP_TOOL_CALL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "tool_name": tool_name,
            "parameters": self._sanitise_params(parameters),
            "actor": "claude",
            "compliance_tags": ["GDPR_ART_25", "READ_ONLY", "ANONYMISED"],
            "server": settings.mcp_server_name,
            "server_version": settings.mcp_server_version,
            "status": status,
            "result_row_count": result_row_count,
            "latency_ms": round(latency_ms, 3),
            "error": error,
        }
        serialised = json.dumps(record, ensure_ascii=False)

        if self._file_handle:
            self._file_handle.write(serialised + "\n")
            self._file_handle.flush()       # fsync on each record for durability
            os.fsync(self._file_handle.fileno())
        else:
            # stdout mode — container runtime captures and forwards to log aggregator
            print(serialised, flush=True)

    def close(self) -> None:
        if self._file_handle:
            self._file_handle.close()


# =============================================================================
# Tool Parameter Models — Strict Pydantic Validation
# =============================================================================

class QueryAnonymizedCustomerDataParams(BaseModel):
    """
    Parameters for the query_anonymized_customer_data tool.

    Pydantic v2 enforces strict types at the boundary. Claude cannot
    supply parameters that would result in a SQL injection vector because:
      1. These values are passed as parameterized query arguments (never interpolated).
      2. Pydantic rejects unexpected types before the query builder sees them.
    """
    region: str = Field(
        description="ISO 3166-1 alpha-2 country code (e.g., 'DE', 'FR', 'NL').",
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
    )
    limit: int = Field(
        default=10,
        description="Maximum number of records to return (hard cap enforced server-side).",
        ge=1,
        le=50,
    )
    segment: str | None = Field(
        default=None,
        description="Optional customer segment filter ('premium', 'standard', 'trial').",
        pattern=r"^(premium|standard|trial)$",
    )

    @field_validator("region", mode="before")
    @classmethod
    def uppercase_region(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.upper().strip()
        return v


# =============================================================================
# Database — Read-Only Connection Pool
# =============================================================================

class ReadOnlyDatabase:
    """
    Manages an asyncpg connection pool constrained to read-only transactions.

    The database user (configured in docker-compose.yml) should also be
    granted SELECT-only privileges at the PostgreSQL role level, providing
    defence-in-depth. This class adds a second layer: even if the role
    misconfiguration allowed writes, the server-side READ ONLY transaction
    mode would prevent them.
    """

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        logger.info("database_connecting", dsn=settings.database_url.split("@")[-1])
        self._pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
            # Force every connection into a read-only transaction mode.
            # This is enforced at the PostgreSQL protocol level.
            server_settings={"default_transaction_read_only": "on"},
        )
        logger.info("database_pool_ready")

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("database_pool_closed")

    async def execute_read_query(
        self,
        query: str,
        *args: Any,
        max_rows: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Execute a parameterized SELECT query and return sanitised results.

        Safety guarantees:
        - asyncpg always uses parameterized queries ($1, $2, …) — no string
          interpolation. SQL injection via parameters is structurally impossible.
        - Connection is in READ ONLY mode; any accidental mutation raises an error.
        - Result set is capped at max_rows to enforce data minimisation.
        """
        if not self._pool:
            raise RuntimeError("Database pool not initialised. Call connect() first.")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            # Cap at max_rows (hard limit, regardless of what Claude requested)
            return [dict(row) for row in rows[:max_rows]]


# =============================================================================
# GDPR Anonymisation Layer
# =============================================================================

# Fields that are NEVER returned to Claude, regardless of what the database
# schema contains. Extend this set as required by your DPIA.
PII_FIELD_BLOCKLIST: frozenset[str] = frozenset({
    "first_name", "last_name", "full_name", "name",
    "email", "email_address",
    "phone", "phone_number", "mobile",
    "address", "street", "postcode", "zip_code",
    "date_of_birth", "dob", "birth_date",
    "national_id", "ssn", "passport_number",
    "iban", "credit_card", "card_number",
    "ip_address", "device_id",
})


def anonymise_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Strip PII fields from a database row before returning to Claude.

    Implements GDPR Article 25 (Data Protection by Design and by Default):
    Only data strictly necessary for the purpose is provided to the AI model.

    Fields in PII_FIELD_BLOCKLIST are replaced with '<ANONYMISED>' rather
    than being dropped entirely, so Claude can understand the schema structure
    without accessing actual personal data.
    """
    return {
        key: ("<ANONYMISED>" if key.lower() in PII_FIELD_BLOCKLIST else value)
        for key, value in row.items()
    }


# =============================================================================
# MCP Server Initialisation
# =============================================================================

db = ReadOnlyDatabase()
audit = AuditLogger()
mcp_app = Server(settings.mcp_server_name)


@asynccontextmanager
async def lifespan(app: Server):  # type: ignore[type-arg]
    """
    Async context manager managing startup/shutdown of the database pool.
    Called automatically by the MCP runtime.
    """
    await db.connect()
    logger.info(
        "mcp_server_started",
        name=settings.mcp_server_name,
        version=settings.mcp_server_version,
    )
    yield
    await db.disconnect()
    audit.close()
    logger.info("mcp_server_stopped")


# =============================================================================
# Tool Definitions — What Claude Can See and Call
# =============================================================================

@mcp_app.list_tools()
async def list_tools() -> list[Tool]:
    """
    Advertise the available tools to Claude.

    The tool descriptions are written from a compliance perspective:
    Claude is explicitly informed that data is anonymised and that
    its actions are being logged. This is good practice for transparency
    in AI-human collaboration.
    """
    return [
        Tool(
            name="query_anonymized_customer_data",
            description=(
                "Query anonymised (GDPR Art. 25 compliant) customer records from the "
                "enterprise database. Raw PII fields (names, emails, phone numbers, etc.) "
                "are automatically stripped before results are returned. All invocations "
                "of this tool are logged to an immutable compliance audit trail. "
                "This tool is READ-ONLY — no data modification is possible."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "ISO 3166-1 alpha-2 country code (e.g., 'DE', 'FR', 'NL').",
                        "pattern": "^[A-Z]{2}$",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of records to return (1–50). Default: 10.",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                    "segment": {
                        "type": "string",
                        "description": "Filter by customer segment: 'premium', 'standard', or 'trial'.",
                        "enum": ["premium", "standard", "trial"],
                    },
                },
                "required": ["region"],
            },
        ),
    ]


# =============================================================================
# Tool Handler — The Audit-Wrapped Core
# =============================================================================

@mcp_app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """
    Central dispatcher for all tool calls from Claude.

    AUDIT GUARANTEE:
    ----------------
    The audit record is written to the sink BEFORE any database operation.
    If the audit write fails (e.g., disk full, SIEM unreachable), the tool
    raises an exception and the query is never executed. This ensures the
    invariant: "no unaudited database access ever occurs."

    The AFTER record (with result_row_count, latency, status) is written
    upon completion, giving operators a complete picture of each interaction.
    """
    session_id = str(uuid.uuid4())
    start_time = time.monotonic()

    # ── STEP 1: Write BEFORE audit record ────────────────────────────────────
    # This happens BEFORE validation and BEFORE query execution.
    # If this write fails, we raise immediately — no query is ever run.
    audit.record(
        session_id=session_id,
        tool_name=name,
        parameters=arguments,
        status="INITIATED",  # pre-execution record
    )

    # ── STEP 2: Validate tool name ────────────────────────────────────────────
    if name != "query_anonymized_customer_data":
        audit.record(
            session_id=session_id,
            tool_name=name,
            parameters=arguments,
            status="BLOCKED",
            error=f"Unknown tool '{name}'. Access denied.",
        )
        raise ValueError(f"Unknown tool: {name}")

    # ── STEP 3: Validate and parse parameters with Pydantic ──────────────────
    try:
        params = QueryAnonymizedCustomerDataParams(**arguments)
    except Exception as exc:
        latency_ms = (time.monotonic() - start_time) * 1000
        audit.record(
            session_id=session_id,
            tool_name=name,
            parameters=arguments,
            status="FAILURE",
            latency_ms=latency_ms,
            error=f"Parameter validation failed: {exc}",
        )
        raise ValueError(f"Invalid parameters: {exc}") from exc

    # ── STEP 4: Execute database query ───────────────────────────────────────
    effective_limit = min(params.limit, settings.query_max_rows)

    # Build a parameterized query — no string interpolation, ever.
    # asyncpg uses $1, $2, … placeholders; values are sent separately
    # over the PostgreSQL binary protocol.
    query = """
        SELECT
            customer_id,
            region,
            segment,
            account_created_at,
            last_activity_at,
            total_orders,
            lifetime_value_eur
        FROM customers
        WHERE region = $1
          AND ($2::text IS NULL OR segment = $2)
        ORDER BY last_activity_at DESC
        LIMIT $3
    """
    try:
        rows = await db.execute_read_query(
            query,
            params.region,
            params.segment,
            effective_limit,
            max_rows=effective_limit,
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - start_time) * 1000
        audit.record(
            session_id=session_id,
            tool_name=name,
            parameters=arguments,
            status="FAILURE",
            latency_ms=latency_ms,
            error=str(exc),
        )
        raise RuntimeError(f"Database query failed: {exc}") from exc

    # ── STEP 5: Apply GDPR anonymisation layer ───────────────────────────────
    anonymised_rows = [anonymise_row(row) for row in rows]

    # ── STEP 6: Write AFTER audit record (SUCCESS) ───────────────────────────
    latency_ms = (time.monotonic() - start_time) * 1000
    audit.record(
        session_id=session_id,
        tool_name=name,
        parameters=arguments,
        status="SUCCESS",
        result_row_count=len(anonymised_rows),
        latency_ms=latency_ms,
    )

    logger.info(
        "tool_call_completed",
        tool=name,
        session_id=session_id,
        rows_returned=len(anonymised_rows),
        latency_ms=round(latency_ms, 1),
    )

    # ── STEP 7: Return anonymised result to Claude ────────────────────────────
    response_payload = {
        "session_id": session_id,
        "tool": name,
        "gdpr_note": (
            "PII fields have been anonymised per GDPR Article 25. "
            "This interaction has been logged to the compliance audit trail."
        ),
        "rows_returned": len(anonymised_rows),
        "results": anonymised_rows,
    }

    return [
        TextContent(
            type="text",
            text=json.dumps(response_payload, indent=2, ensure_ascii=False, default=str),
        )
    ]


# =============================================================================
# Entry Point
# =============================================================================

async def main() -> None:
    """
    Start the MCP server using the stdio transport.

    In production deployments, this process is typically wrapped by the
    MCP host (Claude Desktop, Anthropic API, or your enterprise MCP proxy)
    which communicates over stdin/stdout following the JSON-RPC protocol.

    For SSE/HTTP transport (network-accessible mode), replace stdio_server
    with the MCP SSE server from the mcp.server.sse module.
    """
    async with stdio_server() as (read_stream, write_stream):
        init_options = InitializationOptions(
            server_name=settings.mcp_server_name,
            server_version=settings.mcp_server_version,
            capabilities=mcp_app.get_capabilities(
                notification_options=None,
                experimental_capabilities={},
            ),
        )
        await mcp_app.run(
            read_stream,
            write_stream,
            init_options,
        )


if __name__ == "__main__":
    asyncio.run(main())
