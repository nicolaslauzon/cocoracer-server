# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT-MAP.md`** at the repo root: it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`** at the repo root: system-wide decisions. Also check `<context>/docs/adr/` for context-scoped decisions in the context you're working in.

If a context's `CONTEXT.md` or `docs/adr/` doesn't exist yet, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure (multi-context)

```
/
├── CONTEXT-MAP.md
├── docs/adr/                    ← system-wide decisions
├── cocoracer/                    ← the racing game engine and CLI (submodule)
│   ├── CONTEXT.md
│   └── docs/adr/
├── server/                       ← the event server teams push their controller to
│   └── CONTEXT.md
└── start_kit/                    ← starter kit a team gets to begin the challenge (no CONTEXT.md: it ships to teams)
```

Neither `server/` nor `start_kit/` has a `docs/adr/` yet; their decisions
live in the root `docs/adr/` until one of them earns its own.

Note: `cocoracer/` is a git submodule with its own repo, remote, and history — its `CONTEXT.md`/`docs/adr/` are maintained there, not from the root.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in the relevant context's `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
