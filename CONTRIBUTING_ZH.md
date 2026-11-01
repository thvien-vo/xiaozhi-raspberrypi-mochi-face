# Contributing

For the main English version, see [CONTRIBUTING.md](./CONTRIBUTING.md).

Thank you for contributing to `py-xiaozhi`. We welcome bug reports, documentation fixes, additional tests, toolchain improvements, and well-scoped new features.

## Before You Start

- Search existing Issues and Pull Requests before submitting to avoid duplicates.
- For major new features, architectural changes, or behavioral modifications, please open an Issue for discussion first.
- Documentation fixes, minor bug fixes, and focused improvements can generally be submitted directly via a Pull Request.

## Development Environment

### Basic Requirements

- Python `>=3.10`
- Git
- Working microphone and speaker if verifying audio capabilities

### Install Dependencies

```bash
git clone https://github.com/huangjunsen0406/py-xiaozhi.git
cd py-xiaozhi

# Base installation
uv sync

# GUI + development tools
uv sync --extra gui --group dev
```

If you do not use `uv`, you can use the following compatible approach:

```bash
pip install -e .
pip install -e '.[gui]'
pip install pytest pytest-asyncio ruff pyinstaller
```

## Development Workflow

1. Fork the repository and create a feature branch from `main`.
2. Keep each change focused on a single issue, feature, or category of documentation update.
3. Follow Conventional Commits for commit messages, e.g., `fix(audio): handle empty frame`.
4. Update documentation synchronously if your changes affect behavior, configuration, or public interfaces.
5. Add or update tests if your changes involve correctness or regression risks.

Recommended branch naming:

- `feature/xxx`
- `fix/xxx`
- `docs/xxx`
- `refactor/xxx`
- `test/xxx`

## Quality Requirements

Before submitting a Pull Request, run the appropriate checks based on the type of change:

```bash
ruff check .
ruff format .
pytest
```

If necessary, also verify the corresponding runtime path, for example:

- GUI changes: run `python main.py`
- CLI changes: run `python main.py --mode cli`
- Protocol changes: verify `websocket` or `mqtt`
- Documentation changes: preview locally under `documents/`

## Pull Request Requirements

Every Pull Request should aim to meet the following requirements:

- Clearly explain the problem background and solution.
- Attach links to related Issues if applicable.
- Provide necessary verification steps, screenshots, or logs to facilitate review.
- Keep the scope of changes reasonable and avoid mixing in unrelated refactoring.
- Follow the existing code style and project structure.

## Maintainer Workflow

Maintainers typically review contributions according to the following workflow:

1. Categorize changes as `bug`, `feature`, `docs`, `refactor`, or `maintenance`.
2. Confirm whether the scope of changes is clear, the issue is reproducible, and the direction aligns with the project roadmap.
3. Check tests, documentation, and verification evidence before deciding whether to request changes or approve.
4. Merge after CI passes and all review feedback has been resolved.
5. Merged changes enter subsequent releases through the normal release process; merging does not imply an immediate release.

## Detailed Documentation

- Project Documentation: <https://huangjunsen0406.github.io/py-xiaozhi/>
- Detailed Contribution Guide (English): <https://huangjunsen0406.github.io/py-xiaozhi/en/contributing>
- Detailed Contribution Guide (Chinese): <https://huangjunsen0406.github.io/py-xiaozhi/contributing>

Thank you again for your contribution.
