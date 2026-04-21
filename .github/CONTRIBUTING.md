# Contributing to Sovereign MCP Blueprints

Thank you for considering a contribution to this project. Given the enterprise security focus of this repository, contributions are held to a high standard.

## Blueprint Submission Checklist

Before opening a PR with a new blueprint, verify every item:

- [ ] **Docker-only runtime** — no host Python/Node.js dependencies required
- [ ] **Read-only by default** — write-capable tools must be gated behind an explicit `ENABLE_WRITE_TOOLS=true` env flag and documented with a threat model
- [ ] **Audit logger is non-bypassable** — the `AuditLogger.record()` call must appear BEFORE any external system call in every tool handler
- [ ] **No secrets in code** — all credentials via environment variables; `.env.example` contains only placeholder values
- [ ] **Health check in docker-compose** — both the MCP server and its dependent services must have `healthcheck` blocks
- [ ] **Non-root container user** — Dockerfile must create and switch to a dedicated UID ≥ 10000
- [ ] **PII blocklist documented** — list which fields are anonymised and why, referencing relevant GDPR articles
- [ ] **Blueprint-level README** — includes architecture description, quick start, environment variables table, and threat model section

## Code Style

- Python 3.12+, async/await throughout
- Pydantic v2 for all tool parameter models
- `structlog` for all application logging (JSON output)
- Type hints on all functions and methods

## Security Reports

**Do not open a public GitHub issue for security vulnerabilities.**  
Use the [Security Report template](./.github/ISSUE_TEMPLATE/security-report.md) or email the maintainers directly.
