# Contributing to jvagent

Thanks for your interest in contributing! This guide covers the practical
workflow. For the architecture and where things live, start with
[`CLAUDE.md`](CLAUDE.md) (the agent/contributor map) and the per-subsystem
`CLAUDE.md` files.

## Code of Conduct

This project adheres to the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating, you are expected to uphold it.

## Development setup

Requires Python 3.8+ (CI runs 3.11/3.12).

```bash
git clone https://github.com/TrueSelph/jvagent.git
cd jvagent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install            # install the git hook
```

Run the example app locally:

```bash
jvagent examples/jvagent_app --debug
```

## Commit gate (run before every commit)

Before **every** `git commit` — not just before opening a PR — both gates below
must pass clean. This includes docs/chore/hotfix commits.

```bash
pre-commit run --all-files            # black, isort, flake8, mypy, detect-secrets
pytest tests/                         # full suite (or the affected slice at minimum)
```

If a hook reformats files (black/isort), re-stage and re-run until it passes with
no changes — a "files were modified by this hook" result is a failure. Do not
`git commit --no-verify`. Before opening a PR, also run:

```bash
jvagent validate examples/jvagent_app # app YAML stays valid
```

Conventions (see [`CLAUDE.md` §6](CLAUDE.md)):

- **Type-annotate everything** — Pydantic and jvspatial rely on it.
- **Use `attribute(...)`** for all persisted Node fields (plain class
  attributes are not persisted).
- **Add a test slice** in `tests/action/{name}/` or `tests/{subsystem}/` for
  any new behavior.
- **Cite `file:line`** in commit messages and PR descriptions when fixing bugs.
- **Stay within the action's directory** — cross-cutting changes should be rare.
- Default to `run_in_background=True` for non-user-facing work.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(orchestrator): add resumable plan continuation
fix(memory): cap pruning per call (conversation.py:312)
docs: document lock-companions
```

Keep commits focused and atomic. Reference issues with `Closes #123`.

## Changelog

User-facing changes get an entry under `## [Unreleased]` in
[`CHANGELOG.md`](CHANGELOG.md), following
[Keep a Changelog](https://keepachangelog.com/).

## ADRs

Architecture Decision Records live in [`.planning/adr/`](.planning/adr/) and are
**immutable once accepted**. To change a decision, write a new ADR that
supersedes the old one — don't edit the original.

## Pull request process

1. Fork and branch from `main` (or the current integration branch).
2. Make your change with tests and docs.
3. Ensure the gates above pass.
4. Open a PR using the template; fill in the checklist.
5. A maintainer ([CODEOWNERS](.github/CODEOWNERS)) will review.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
