# Contributing to blocks-genesis-py

Thank you for your interest in contributing to **blocks-genesis-py**! Your contributions help improve this reusable FastAPI utility package for everyone. Whether you're reporting a bug, suggesting an enhancement, or submitting code changes, we welcome your input.

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
3. **Create a Branch**: Create a new branch for your feature or bugfix (see [Branching Strategy](#branching-strategy)).
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Set up Development Environment**: Follow [Development Setup](#development-setup).
5. **Make Changes**: Implement your changes following [Coding Guidelines](#coding-guidelines).
6. **Write/Update Tests**: Ensure new code has tests (see [Testing](#testing)).
7. **Run Tests**: Verify all tests pass locally.
   ```bash
   pytest
   ```
8. **Commit Changes**: Follow [Git Guidelines](#git-guidelines) for commit messages.
9. **Push to GitHub**: Push your changes to your forked repository.
   ```bash
   git push origin feature/your-feature-name
   ```
10. **Open a Pull Request**: Navigate to the original repository and click "New Pull Request". Link any related issues.

## Development Setup

### 1. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

Using `uv` (recommended):
```bash
pip install uv
uv pip install -e .
uv pip install pytest
```

Using `pip`:
```bash
pip install -e .
pip install pytest
```

### 3. Verify Installation

```bash
pytest tests/ -v
```

## Branching Strategy

We follow **Git Flow** for branching:

- `main`: Production-ready, stable releases.
- `dev`: Active development branch (default for PRs).
- `feature/*`: New features branching from `dev` (e.g., `feature/kb-ingestion`).
- `bugfix/*`: Bug fixes branching from `dev` (e.g., `bugfix/sse-stream-timeout`).
- `hotfix/*`: Emergency fixes branching from `main` for critical production issues.
- `docs/*`: Documentation updates (e.g., `docs/api-reference`).

All PRs should target the `dev` branch unless otherwise agreed.

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
feat(agents): add agent publish to marketplace

- Add publish endpoint that changes agent status
- Include validation for required fields
- Update agent model with marketplace metadata

Closes #42
```

```
fix(kb): resolve sse stream timeout on large file ingestion

The vector embedding batch was timing out for files >100MB.
Split batches into smaller chunks and add exponential backoff.

Fixes #189
```

```
docs: update api endpoint documentation

Update conversation routes with new session response schema.
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
2. Keep request handlers in `api.py` or in reusable helpers under `blocks_genesis/_core/` where appropriate.
3. Add/extend worker event handling through `worker.py`, `test_consumer.py`, and `blocks_genesis/_message/`.
4. Add corresponding tests under `tests/` for all behavior changes.

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

- **Framework**: Use `pytest` with `pytest-asyncio` for async tests.
- **File Naming**: Test files should be named `test_*.py` or `*_test.py`.
- **Function Naming**: Test functions should be named `test_*`.
- **Fixtures**: Use `conftest.py` for shared fixtures.
- **Mocking**: Use `pytest-mock` for mocking dependencies.

Example:

```python
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_create_agent(mock_service):
    """Test agent creation endpoint."""
    request = CreateAgentRequest(name="Test Agent", ...)
    response = await create_agent(request, service=mock_service)
    
    assert response.is_success
    mock_service.create_agent.assert_called_once()
```

### Running Tests

Run all tests:
```bash
pytest
```

Run specific test file:
```bash
pytest tests/test_api.py
```

Run with coverage:
```bash
pytest --cov=blocks_genesis --cov-report=html
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
   - Once approved and all checks pass, the PR is merged into `dev`.
   - Use "Squash and merge" for feature PRs to keep history clean.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).

