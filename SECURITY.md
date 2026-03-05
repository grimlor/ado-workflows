# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
responsibly by emailing the maintainer directly rather than opening a public
issue.

## Scope

This library wraps the Azure DevOps Python SDK (`azure-devops`) and handles
authentication via `azure-identity`. Security considerations:

- **Authentication tokens** — `ConnectionFactory` acquires OAuth tokens via
  `DefaultAzureCredential`. Tokens are cached per-org and refreshed
  automatically. Leaked tokens grant Azure DevOps API access.
- **Error messages** — `ActionableError` instances may carry SDK exception
  messages containing URLs, project names, or repository identifiers.
  Consumers should apply `actionable_errors.sanitizer` before logging or
  returning errors to external callers.
- **Repository discovery** — `inspect_git_repository()` reads git remote
  URLs from the local filesystem. In shared environments, ensure the
  working directory is trusted.

## Best Practices

- Use `DefaultAzureCredential` (managed identity in CI, Azure CLI locally)
  rather than hardcoded PATs
- Apply the `actionable-errors` credential sanitizer to error messages
  before exposing them to AI agents or API responses
- Do not log or persist raw OAuth tokens
