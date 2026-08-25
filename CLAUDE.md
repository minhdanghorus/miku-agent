# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

miku-agent is at the scaffolding stage: `main.py` is a `print()` stub, `pyproject.toml` declares no dependencies, and there is no package layout, test suite, or lint config yet. There is no architecture to discover from the code — the intent lives in the README and in OpenSpec changes.

Goal (README): a local-first AI agent harness — agent loop, memory, and eval — that stays legible as it grows. When adding structure, prefer explicit, readable code over framework indirection; that legibility goal is the project's stated design constraint.

## Environment and commands

Python 3.13 (`.python-version`), managed with `uv`; a `.venv` is already present.

```bash
uv run main.py            # run the entry point
uv sync                   # install/refresh deps after editing pyproject.toml
uv add <pkg>              # add a dependency (edits pyproject.toml + uv.lock)
```

No test runner or linter is configured yet. If tests are needed, add pytest via `uv add --dev pytest` and run `uv run pytest` (`uv run pytest path/to/test_x.py::test_name` for a single test) — but confirm the choice against any active OpenSpec change first rather than introducing tooling ad hoc.

## OpenSpec workflow

This repo uses OpenSpec (`openspec/config.yaml`, schema `spec-driven`) with skills and `/opsx:*` commands under `.claude/`. Feature work is expected to go through it rather than straight to code:

- `/opsx:explore` — think through a problem before committing to a plan
- `/opsx:propose` — create a change with proposal.md, design.md, tasks.md
- `/opsx:apply` — implement the change's tasks
- `/opsx:sync` / `/opsx:archive` — fold delta specs into main specs, then archive

Practical notes when driving it:

- The `openspec` CLI is the source of truth for paths. Use `openspec status --change <name> --json` and `openspec instructions <artifact> --change <name> --json`, and write to the `resolvedOutputPath` they report — do not assume repo-relative locations.
- `context` and `rules` from the instructions JSON are constraints on what you write, never content to copy into the artifact file.
- `openspec/config.yaml` currently has `context:` and `rules:` commented out. Once the stack and conventions settle, filling in `context:` is the right place to record them — it is injected into every artifact generation.
