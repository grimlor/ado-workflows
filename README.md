# ado-workflows

Azure DevOps workflow automation library — PR context, repository discovery, and SDK client wrappers.

[![CI](https://github.com/grimlor/ado-workflows/actions/workflows/ci.yml/badge.svg)](https://github.com/grimlor/ado-workflows/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ado-workflows)](https://pypi.org/project/ado-workflows/)

## Install

```bash
pip install ado-workflows
```

## Architecture

Three-layer API:

| Layer | Purpose | State | SDK dependency |
|-------|---------|-------|----------------|
| **1 — Primitives** | URL parsing, git inspection, date parsing | None | None |
| **2 — Context** | Repository context caching | Thread-safe | None |
| **3 — PR Context** | Composed PR workflows | Composes L1+L2 | `azure-devops` |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full design rationale.

## Development

```bash
uv sync --extra dev
task check          # lint + type-check + test
```

## License

MIT
