# 🚧 local-gitlab-mcp — Coming Soon

This blueprint will provide a Sovereign MCP server for **self-hosted GitLab CE/EE** instances.

## Planned Features

- Browse repositories, branches, and commits (read-only)
- List and search issues and merge requests
- View CI/CD pipeline status
- Retrieve file contents from specific refs
- Full audit logging on every GitLab API call
- No connection to gitlab.com — 100% self-hosted

## Configuration Preview

```yaml
# docker-compose.yml (preview)
environment:
  GITLAB_URL: "https://gitlab.internal.company.com"
  GITLAB_TOKEN_SECRET: "/run/secrets/gitlab_pat"  # Docker secret
  ALLOWED_GROUPS: "engineering,data-science"       # Allowlist
  AUDIT_LOG_MODE: "stdout"
```

## Contribute

If you'd like to accelerate this blueprint, open an issue or submit a PR.
See [CONTRIBUTING.md](../../.github/CONTRIBUTING.md) for guidelines.
