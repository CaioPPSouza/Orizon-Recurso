# Repository Guidelines

## Project Structure & Module Organization
This repository is currently a clean scaffold (no source files yet). Keep the layout predictable as code is added:
- `src/`: application or library source code.
- `tests/`: automated tests mirroring `src/` paths.
- `assets/`: static files (images, fixtures, sample data).
- `docs/`: architecture notes, decisions, and usage docs.

Example layout:
`src/<module>/...`, `tests/<module>/test_<feature>.*`.

## Build, Test, and Development Commands
No build system is configured yet. When adding one, expose a small, stable command set and document it here. Recommended baseline:
- `npm run dev` or `make dev`: run local development workflow.
- `npm test` or `make test`: run the full test suite.
- `npm run lint` or `make lint`: run static analysis/format checks.
- `npm run build` or `make build`: produce distributable artifacts.

Prefer commands that run cross-platform and work from repository root.

## Coding Style & Naming Conventions
- Use consistent formatting with an auto-formatter (for example, Prettier, Black, or gofmt).
- Use lints in CI (for example, ESLint, Ruff, or golangci-lint).
- Indentation: 2 spaces for JS/TS/JSON/YAML, 4 spaces for Python.
- Naming:
  - files/modules: `kebab-case` (web) or `snake_case` (Python),
  - classes/types: `PascalCase`,
  - functions/variables: `camelCase` (JS/TS) or `snake_case` (Python).

## Testing Guidelines
Add unit tests for every new feature and bug fix. Suggested conventions:
- test files: `*.test.ts`, `test_*.py`, or equivalent for the chosen stack.
- keep tests deterministic and fast; isolate external dependencies.
- target meaningful coverage for changed code paths before merge.

## Commit & Pull Request Guidelines
There is no commit history yet; adopt Conventional Commits from the start:
- `feat: add user import service`
- `fix: handle null response in parser`
- `docs: add setup instructions`

PRs should include:
- concise description of what changed and why,
- linked issue/ticket (if available),
- test evidence (command output or CI link),
- screenshots for UI changes.

## Security & Configuration Tips
Do not commit secrets. Use `.env.example` for required variables and keep real values in local `.env` files ignored by Git. Pin dependency versions where practical and review updates regularly.

## Agent Communication Preference
The user speaks Brazilian Portuguese (`pt-BR`). All current and future communication must be written in Brazilian Portuguese.
