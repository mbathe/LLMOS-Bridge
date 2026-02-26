# Contributing to LLMOS Bridge

## Development setup

```bash
git clone https://github.com/llmos-bridge/llmos-bridge
cd llmos-bridge
scripts/setup-dev.sh
```

## Repository structure

```
llmos-bridge/
├── packages/
│   ├── llmos-bridge/          Core daemon
│   ├── langchain-llmos/       LangChain SDK
│   └── llmos-module-template/ Community module template
├── docs/
│   ├── protocol/              IML schema reference
│   └── api/                   HTTP API reference
└── scripts/                   Development utilities
```

## Branch strategy

- `main` — stable, released code
- `develop` — integration branch for new features
- `feat/<name>` — feature branches
- `fix/<name>` — bug fix branches
- `module/<name>` — new module branches

## Commit convention

```
type(scope): short description

Types: feat, fix, refactor, test, docs, chore, perf
Scopes: protocol, security, orchestration, modules, api, cli, sdk

Examples:
  feat(modules): add Excel read_range action
  fix(protocol): reject plans with cyclic dependencies
  test(orchestration): add DAG cycle detection tests
```

## Adding a new module

1. Copy `packages/llmos-module-template/`
2. Rename to `llmos-module-<your-module>`
3. Implement `BaseModule` interface
4. Add typed `Params` models in `params.py`
5. Declare platform support in `get_manifest()`
6. Achieve >= 80% test coverage
7. Add at least 2 usage examples in `examples/`
8. Open a PR against `develop`

## Code standards

- Python 3.11+ — use `X | Y` union syntax, `match` statements where appropriate
- All public functions and classes must have type annotations
- `ruff` for linting and formatting — `make lint` must pass
- `mypy --strict` must pass — no `# type: ignore` without justification
- All new code requires tests — no exceptions

## Running tests

```bash
# Unit tests only (fast, no I/O)
pytest -m unit

# Integration tests (real filesystem/OS, no network)
pytest -m integration

# Full suite
pytest

# With coverage
pytest --cov=llmos_bridge --cov-report=html
```

## Security

If you discover a security vulnerability, do NOT open a public issue.
Send a report to security@llmos-bridge.io with full details.
We follow a 90-day disclosure policy.
