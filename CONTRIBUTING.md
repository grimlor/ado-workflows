# Contributing

Thanks for your interest in contributing to ado-workflows. This document
covers the development setup, coding standards, testing philosophy, and
PR process.

---

## Development Setup

```bash
# Clone
git clone https://github.com/grimlor/ado-workflows.git
cd ado-workflows

# Install with dev dependencies (creates .venv automatically)
uv sync --extra dev

# Optional: auto-activate venv
direnv allow
```

## Running Checks

All checks must pass before submitting a PR:

```bash
task check          # runs lint → type → test
```

Or individually:

```bash
task lint           # ruff check src/ tests/
task format         # ruff format src/ tests/
task type           # mypy strict mode
task test           # pytest -v
task cov            # pytest with coverage report
```

## Code Style

- **Python 3.12+** — use modern syntax (`X | Y` unions, `@dataclass`).
- **`from __future__ import annotations`** at the top of every module.
- **ruff** handles formatting and import sorting. Don't fight it.
- **mypy strict** — all functions need type annotations. No `Any` unless
  you have a good reason and document it.
- **Line length:** 99 characters (configured in `pyproject.toml`).
- **Quote style:** double quotes.

## Testing Standards

Tests are the living specification. Every test class documents a behavioral
requirement, not a code structure.

### Test Class Structure

```python
class TestYourFeature:
    """
    REQUIREMENT: One-sentence summary of the behavioral contract.

    WHO: Who depends on this behavior (calling code, operator, AI agent)
    WHAT: What the behavior is, including failure modes
    WHY: What breaks if this contract is violated

    MOCK BOUNDARY:
        Mock:  client.git.some_sdk_method (SDK I/O edge)
        Real:  your_function, dataclass construction
        Never: N/A
    """

    def test_descriptive_name_of_scenario(self) -> None:
        """
        Given some precondition
        When an action is taken
        Then an observable outcome occurs
        """
        ...
```

### Key Principles

1. **Mock I/O boundaries, not implementation.** Mock the Azure DevOps SDK
   client methods (`client.git.*`, `client.core.*`) — never mock internal
   functions or dataclass construction.

2. **Failure specs matter.** For every happy path, ask: what goes wrong?
   Write specs for those failure modes. An unspecified failure is an
   unhandled failure.

3. **Missing spec = missing requirement.** If you find a bug, the first
   step is always adding the test that should have caught it, then fixing
   the code to pass that test.

4. **Every assertion includes a diagnostic message.** Bare assertions are
   not permitted.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the three-layer API
design and module responsibilities.

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat: add PR lifecycle operations — create, comment, reply, resolve

- create_pull_request() with branch normalization
- post_comment() with content validation
- resolve_comments() with partial-success semantics
```

Common prefixes: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `refactor:`,
`style:`, `ci:`, `chore:`.

## Pull Requests

1. **Branch from `main`.**
2. **All checks must pass** — `task check` (lint + type + test).
3. **Include tests** for any new behavior or bug fix.
4. **One concern per PR** — don't mix a new feature with unrelated refactoring.
5. **Describe what and why** in the PR description.

## Reporting Issues

When filing an issue:

- **Bug:** Include the error message, what you expected, and steps to
  reproduce. Include the Python version and how ado-workflows was
  installed.
- **Feature request:** Describe the problem you're trying to solve, not
  just the solution you have in mind.
