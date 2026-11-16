# Issue tracker: Local Markdown

Issues and specs live as markdown files in `.scratch/`, by choice: both
repos have GitHub remotes, but the maintainer's own notes are not filed
there. `.scratch/` is local to the maintainer's checkout: each repo's
`.gitignore` lists it, so it is not tracked and does not ship with the
repo. A fresh clone has none; create it as below.

## Which `.scratch`

- Root `.scratch/`: work on `server/`, `start_kit/`, and anything that
  spans both repos.
- `cocoracer/.scratch/`: cocoracer-only work, ignored by the submodule's
  `.gitignore` likewise.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- The spec and every ticket carry a `Status:` line near the top (values below); a spec that lists its tickets in a table keeps that table's status column in step
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## Status values

| Status | Meaning |
|---|---|
| `ready` | Spec written, its tickets still open |
| `ready-for-agent` | Fully specified, ready for an AFK agent |
| `claimed` | An agent is working on it; set before any work |
| `done` | Finished |
| `done (by-hand check pending: …)` | Finished, except a check only a human can run; say which |
| `done (…)` | Finished, with a note on what was checked or left over |
| `dropped (…)` | Not done, on the maintainer's decision; say why |
| `wontfix (…)` | Not a bug, or not worth fixing; say why |
| `superseded by <path>` | Replaced by another ticket or effort |

When a triage skill names a role, use the matching value: AFK-ready is
`ready-for-agent`, won't-fix is `wontfix`; `needs-triage`, `needs-info` and
`ready-for-human` are written as-is.

Older files also carry `resolved`, `fixed by <ticket>` and `closed: …`;
read them as `done`, and write the values above.

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.
