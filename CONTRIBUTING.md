# Contributing to blocks-genesis-py

Thank you for your interest in contributing to **blocks-genesis-py**! Your contributions help improve this reusable FastAPI utility package for everyone. Whether you're reporting a bug, suggesting an enhancement, or submitting code changes, we welcome your input.

> **Public API stability warning**: this package is consumed by ten downstream SELISE Blocks repositories. Any change to a name exported from the top-level `blocks_genesis` package (its `__all__`), or to a signature, type, default value, or observable behavior of those exports, is a breaking change for every consumer. Do not make such a change casually; raise it first in an issue so it can be coordinated across all consumers.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Contribute](#how-to-contribute)
  - [Reporting Issues](#reporting-issues)
  - [Submitting Pull Requests](#submitting-pull-requests)
- [Development Setup](#development-setup)
- [Branching Strategy](#branching-strategy)
- [Git Guidelines](#git-guidelines)
- [Coding Guidelines](#coding-guidelines)
- [Testing](#testing)
- [Code Review Process](#code-review-process)
- [License](#license)

## Code of Conduct

Please read and follow our [Code of Conduct](./CODE_OF_CONDUCT.md). By participating in this project, you agree to abide by its terms.

## How to Contribute

### Reporting Issues

If you encounter a bug or have a feature request, please [open an issue](https://github.com/SELISEdigitalplatforms/blocks-genesis-py/issues/new) and include:

**For Bugs:**
- **Description**: Clear, concise description of the issue
- **Steps to Reproduce**: Detailed steps to replicate the problem
- **Expected Behavior**: What should happen
- **Actual Behavior**: What actually happens
- **Environment**: Python version, OS, Docker version (if applicable), Python dependencies versions
- **Logs/Error Output**: Relevant error messages or stack traces
- **Type**: Label as `bug`

**For Feature Requests:**
- **Use Case**: Clear explanation of the feature and its use case
- **Proposed Solution**: Your suggested implementation (if any)
- **Alternative Approaches**: Any alternative approaches considered
- **Type**: Label as `enhancement`

### Submitting Pull Requests

1. **Fork the Repository**: Click the "Fork" button at the top right of the repository page.
2. **Clone Your Fork**: Clone your forked repository to your local machine.
   ```bash
   git clone https://github.com/SELISEdigitalplatforms/blocks-genesis-py.git
   cd blocks-genesis-py
   ```
3. **Create a Branch**: Branch from `inception` for your feature or bugfix (see [Branching Strategy](#branching-strategy)).
   ```bash
   git checkout inception
   git checkout -b your-branch-name
   ```
4. **Set up Development Environment**: Follow [Development Setup](#development-setup).
5. **Make Changes**: Implement your changes following [Coding Guidelines](#coding-guidelines).
6. **Write/Update Tests**: Ensure new code has tests (see [Testing](#testing)).
7. **Run Tests**: Verify all tests pass locally.
   ```bash
   uv run pytest
   ```
8. **Commit Changes**: Follow [Git Guidelines](#git-guidelines) for commit messages.
9. **Push to GitHub**: Push your changes to your forked repository.
   ```bash
   git push origin your-branch-name
   ```
10. **Open a Pull Request**: Navigate to the original repository and open a pull request targeting `inception`. Link any related issues.

## Development Setup

The repository is managed with [uv](https://docs.astral.sh/uv/) and pins Python 3.12 (`.python-version`). Dependencies, including the dev group (pytest, pytest-asyncio, pytest-cov, httpx, twine), are locked in `uv.lock`.

### 1. Install Dependencies

```bash
uv sync
```

This creates `.venv` and installs the exact locked versions.

### 2. Verify Installation

```bash
uv run pytest
```

## Branching Strategy

This repository uses a two-branch model:

- `main`: The default branch. Production-ready, stable releases; protected.
- `inception`: The active development branch. Day-to-day work is committed here.

Work lands on `inception`, and pull requests are opened from `inception` into `main`. External contributors should fork the repository, branch from `inception`, and target `inception` with their pull requests.

## Git Guidelines

We follow **Conventional Commits** specification for standardized commit messages.

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Changes that don't affect code logic (formatting, whitespace, semicolons)
- `refactor`: Code change that refactors without feature/fix (no functional changes)
- `perf`: Performance improvements
- `test`: Adding/updating tests
- `chore`: Build process, dependency updates, tooling changes

### Scope (optional)

Indicate the affected component or module:
- `auth`: Authentication and authorization utilities
- `cache`: Redis cache provider/client logic
- `core`: App/worker bootstrapping and lifecycle
- `database`: Mongo context and subscribers
- `message`: Broker clients and consumer/publisher flow
- `tenant`: Tenant model/service and middleware behavior
- `middlewares`: Cross-cutting middleware changes
- `lmt`: Logging, metrics, and tracing components
- `utilities`: Shared utility helpers
- `tests`: Test coverage and fixtures
- `config`: Environment/runtime configuration

### Subject Line

- Use imperative mood ("add feature", not "added feature")
- Do not capitalize first letter
- Do not end with a period
- Maximum 50 characters
- Be specific and descriptive

### Body

- Use imperative mood
- Explain **what** and **why**, not **how**
- Wrap at 72 characters
- Separate each logical change with a blank line

### Footer

Reference related issues or breaking changes:
```
Fixes #123
Closes #456
BREAKING CHANGE: description of breaking change
```

### Examples

```
feat(message): add rabbitmq consumer subscription binding via exchange

- Add bind_to_queue_via_exchange factory on ConsumerSubscription
- Support parallel processing flag per subscription

Closes #42
```

```
fix(cache): support user= alias in redis connection strings

redis-py expects the username key, but some connection strings
use user. Map the alias before building the client config.

Fixes #189
```

```
docs: correct key vault environment variable reference

Document KEYVAULT__KEYVAULTURL and DefaultAzureCredential usage.
```

## Coding Guidelines

### Python Style and Format

- **PEP 8 Compliance**: Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guidelines.
- **Line Length**: Maximum 120 characters (project convention).
- **Imports**: 
  - Organize imports in three groups: standard library, third-party, local (separated by blank lines).
  - Use absolute imports.
  - Avoid circular imports.
- **Async/Await**: Use `async`/`await` consistently for async functions. Ensure proper exception handling in async contexts.
- **Type Hints**: Use type hints for all function parameters and return types (PEP 484).
  ```python
  async def get_agents(query: GetAgentsRequest) -> GetAgentsResponse:
      """Retrieve agents based on query filters."""
      pass
  ```

### Project Structure

When adding new features, follow the existing structure:

```
api.py
worker.py
blocks_genesis/
├── _auth/
├── _cache/
├── _core/
├── _database/
├── _lmt/
├── _message/
├── _middlewares/
├── _tenant/
└── _utilities/
tests/
config/
```

**For new features:**
1. Add or extend modules under `blocks_genesis/<domain>/` with clear responsibility boundaries.
2. `api.py`, `worker.py`, and `test_consumer.py` at the repository root are reference samples, not part of the published package; update them only to demonstrate new capabilities.
3. Add corresponding tests under `tests/` for all behavior changes.
4. Remember the public API stability warning at the top of this document before touching anything exported from `blocks_genesis`.

### API Conventions

- **Endpoint Naming**: Use RESTful conventions with resource names and HTTP verbs.
- **Response Models**: Use Pydantic models for request/response validation.
- **Status Codes**: 
  - `200 OK` for successful GET/PATCH
  - `201 Created` for successful POST
  - `204 No Content` for successful DELETE
  - `400 Bad Request` for validation errors
  - `404 Not Found` for missing resources
  - `500 Internal Server Error` for server errors
- **Error Responses**: Return structured error responses (see existing patterns in routes).
- **Documentation**: Add docstrings and OpenAPI descriptions to all endpoints.
  ```python
  @app.get("/health")
  async def health() -> dict:
     """Return service health status."""
     return {"status": "healthy"}
  ```

### Logging

- Use Python's `logging` module (not print statements).
- Initialize logger: `logger = logging.getLogger(__name__)`
- Use appropriate log levels: `debug`, `info`, `warning`, `error`, `critical`.
  ```python
  logger.info("Agent created: %s", agent_id)
  logger.error("Failed to create agent: %s", error_detail)
  ```

### Context and Multi-Tenancy

- Use `get_configurations()` for accessing runtime config.
- Avoid hardcoding tenant IDs or project-specific values.

### Error Handling

- Use FastAPI's `HTTPException` for HTTP-level errors.
- Provide meaningful error messages.
- Log exceptions with full context.
  ```python
  if not payload.get("message"):
     raise HTTPException(status_code=400, detail="message is required.")
  ```

## Testing

### Test Organization

Tests are organized in `tests/` to mirror source structure:

```
tests/
├── test_api.py
├── test_auth.py
├── test_worker.py
└── test_*.py modules for each package component
```

### Writing Tests

- **Framework**: Use `pytest` with `pytest-asyncio` for async tests (`asyncio_mode = "auto"` is set in `pyproject.toml`, so plain `async def test_*` functions work without a marker).
- **File Naming**: Test files should be named `test_*.py` or `*_test.py`.
- **Function Naming**: Test functions should be named `test_*`.
- **Mocking**: Use `unittest.mock` (`MagicMock`, `AsyncMock`, `patch`) for mocking dependencies, as the existing tests do.

Example:

```python
from unittest.mock import AsyncMock

from blocks_genesis import CryptoService


def test_hash_string_is_deterministic():
    """The same input and salt must always produce the same digest."""
    assert CryptoService.hash_string("value", "salt") == CryptoService.hash_string("value", "salt")


async def test_async_dependency_called_once():
    service = AsyncMock()
    await service.do_work()
    service.do_work.assert_awaited_once()
```

### Running Tests

Run all tests (from the repository root):
```bash
uv run pytest
```

Run a specific test file:
```bash
uv run pytest tests/test_api.py
```

Run with coverage:
```bash
uv run pytest --cov=blocks_genesis --cov-report=html
```

### Test Requirements

- New features must include tests.
- Bug fixes should include regression tests.
- Aim for >80% code coverage on service layers.
- All tests must pass before PR submission.

## Code Review Process

All PRs undergo review to maintain quality:

1. **PR Submission**: 
   - Ensure PR is focused on a single feature/fix.
   - Link related issues.
   - Provide clear description of changes.
   - Verify all tests pass locally.

2. **Automated Checks**: 
   - CI/CD will run tests and linting.
   - Code must pass all checks.

3. **Peer Review**: 
   - At least one maintainer must review and approve.
   - Address review comments promptly.
   - Request re-review after making changes.

4. **Merge Process**: 
   - Once approved and all checks pass, the PR is merged into `inception`; releases flow from `inception` into `main` via pull request.
   - Use "Squash and merge" for feature PRs to keep history clean.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).

