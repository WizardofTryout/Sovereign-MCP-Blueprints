# =============================================================================
# Sovereign MCP Blueprints — auditable-sql-mcp
# Dockerfile — Hardened Python Container
#
# Security hardening applied:
#   - Distroless-style: non-root user, minimal attack surface
#   - Multi-stage build: no build tools in final image
#   - Read-only filesystem (docker-compose enforces read_only: true)
#   - All capabilities dropped (cap_drop: ALL in docker-compose)
# =============================================================================

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies (only needed to compile asyncpg C extension)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Create a dedicated non-root user for the MCP server process.
# UID/GID 10001 is chosen to avoid conflicts with common system UIDs.
RUN groupadd --gid 10001 mcpgroup \
    && useradd --uid 10001 --gid mcpgroup --no-create-home mcpuser

WORKDIR /app

# Copy only the installed packages from the builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./

# Set ownership to non-root user
RUN chown -R mcpuser:mcpgroup /app

USER mcpuser

# Metadata labels (OCI Image Spec)
LABEL org.opencontainers.image.title="Sovereign SQL MCP Server" \
      org.opencontainers.image.description="GDPR-compliant, auditable MCP server for PostgreSQL" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/your-org/Sovereign-MCP-Blueprints"

# Health check endpoint is handled by uvicorn in server.py
EXPOSE 8000

# Unbuffered output ensures log lines are never lost in container crashes
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "server.py"]
