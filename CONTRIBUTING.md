# Contributing to cytario-app-sdk

Thank you for your interest in contributing to cytario-app-sdk! This document
provides guidelines and information for contributors.

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Install dependencies with `uv sync`
4. Create a new branch for your feature or fix

## Development Setup

This project uses [`uv`](https://docs.astral.sh/uv/) and targets Python 3.10+.

```sh
uv sync
uv run cytario-app-sdk --help
```

## Commit Guidelines

We use [Conventional Commits](https://www.conventionalcommits.org/) for commit
messages. Releases are cut automatically by
[python-semantic-release][psr] from the commit history on `main`.

**Allowed types:**

- `feat`: A new feature (minor bump)
- `fix`: A bug fix (patch bump)
- `perf`: A performance improvement (patch bump)
- `docs`: Documentation only changes
- `style`: Formatting, white-space, etc (no code change)
- `refactor`: Code changes that neither fix a bug nor add a feature
- `test`: Adding or fixing tests
- `build`: Build system or dependencies changes
- `ci`: Continuous Integration config changes
- `chore`: Other changes that don't modify src or test files
- `revert`: Reverts a previous commit

A breaking change is signaled with a `!` after the type (e.g.
`feat!: drop Python 3.9 support`) or a `BREAKING CHANGE:` footer, and triggers a
major version bump.

[psr]: https://github.com/python-semantic-release/python-semantic-release

## Pull Request Process

1. Ensure your code follows the existing style and conventions
2. Add tests for new functionality — HTTP is mocked with
[pytest-httpx](https://github.com/Colin-b/pytest_httpx)
3. Ensure all tests pass with `uv run pytest`
4. Ensure linting and formatting pass:
`uv run ruff check --fix && uv run ruff format`
5. Submit your pull request with a clear description of changes

## Code Style

- Linting and formatting use [Ruff](https://docs.astral.sh/ruff/) with
`select = ["ALL"]` and a handful of intentional per-file ignores (see
`pyproject.toml`). Do not "clean up" the test-suite ignores.
- Pydantic models use `extra="forbid"` and camelCase aliases
(`mediaTypes`, `schemaVersion`, …) because the app-definition YAML is
camelCase. Add new fields with `alias=` or validation will reject them.
- Canonical JSON matters: both `oci/client.py` and `cli.py` serialize the
definition/manifest with `sort_keys=True, separators=(",", ":")` so the
manifest digest is stable across runs.

## Reporting Issues

When reporting issues, please include:

- A clear description of the problem
- Steps to reproduce the issue
- Expected vs actual behavior
- Environment details (OS, Python version, registry/version)

## Releasing

Releases are fully automated. Pushing to `main` runs
[`.github/workflows/release.yml`](.github/workflows/release.yml), which:

1. Runs `python-semantic-release` to bump the version (driven by Conventional
Commits), update `CHANGELOG.md`, stamp `pyproject.toml` and
`src/cytario_app_sdk/__init__.py`, build the sdist + wheel, and publish a
GitHub release with the changelog.
2. Publishes the built artifacts to [PyPI][pypi] using
[trusted publishing][trusted-publishing] (OIDC) — no API token is stored in
the repo. The first release requires a one-time setup of the project as a
PyPI trusted publisher (see the PyPA guide).

[pypi]: https://pypi.org/project/cytario-app-sdk/
[trusted-publishing]: https://docs.pypi.org/trusted-publishers/

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
