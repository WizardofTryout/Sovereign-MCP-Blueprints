# 🚧 secure-files-mcp — Coming Soon

This blueprint will provide a Sovereign MCP server for **allowlisted filesystem access** to enterprise NFS, SMB, and S3-compatible storage.

## Planned Features

- Read files from pre-approved directories only (no path traversal possible)
- List directory contents with metadata
- Search file contents (grep-style, regex-capable)
- Support for NFS mounts, SMB shares, and S3-compatible endpoints (MinIO)
- Path allowlist enforced at container mount level AND application level
- Full audit logging on every file access

## Security Model Preview

```yaml
# docker-compose.yml (preview)
volumes:
  - /mnt/nfs/approved-reports:/data/reports:ro   # Read-only mount
  - /mnt/nfs/approved-docs:/data/docs:ro
environment:
  ALLOWED_PATHS: "/data/reports,/data/docs"      # Application-level allowlist
  MAX_FILE_SIZE_MB: "10"                         # Data minimisation
```

## Contribute

Open an issue or submit a PR. See [CONTRIBUTING.md](../../.github/CONTRIBUTING.md).
