<!--
  Auto-generated guidance for AI coding agents working in this repository.
  This file was created because the repository currently has no inspectable
  code or config files. Follow the discovery checklist below and ask the
  human owner for clarification before making broad assumptions.
-->

# Copilot / AI Agent Instructions

Purpose
- Quickly orient an AI agent to be productive in this repo. The repository
  is currently empty; these instructions prioritize discovery, clarifying
  questions, and safe, minimal scaffolding if asked to initialize the project.

Immediate state
- The workspace contains no source files, configs, or workflows. Before writing
  code, ask the repo owner: what is the project type (Node app, library,
  monorepo, service, frontend, etc.) and what language/tooling they prefer?

Discovery checklist (ordered)
- Check for a `package.json` at the repo root. If present: inspect `scripts`,
  `dependencies`, and `devDependencies`. Common helpful scripts: `start`,
  `build`, `test`, `lint`.
- Look for `src/`, `lib/`, `cmd/`, or `app/` directories to find entry points.
- Check for language configs: `tsconfig.json`, `.eslintrc`, `.prettierrc`,
  `pyproject.toml`, `go.mod`, `Dockerfile`, or `.github/workflows/*.yml`.
- If `README.md` exists, use it as the primary source of intent and run
  instructions; follow any developer commands shown there.

If `package.json` exists (example actions)
- Run `npm ci` or `npm install` locally (ask before running in CI).
- Use `npm run test` if a `test` script is defined; if not, do not invent tests.

Scaffolding rules (only when explicitly requested)
- Ask before initializing a project. If the owner asks to scaffold a Node
  application, create a minimal structure: `package.json`, `src/index.js` or
  `src/index.ts`, a basic `.gitignore`, and a short `README.md` describing how
  to run the app. Use the owner's language preference.
- Keep changes minimal and isolated in a single commit. Add tests for any
  non-trivial code you add.

Code and style conventions
- If ESLint / Prettier / EditorConfig exist, follow those rules. If none
  exist, match the existing project's style; if the repo is empty, prefer
  widely-compatible defaults (ES2020, Prettier formatting) and document them.

PR and commit guidance
- Make small, focused commits. Each PR should include a short description of
  intent, the files changed, and the manual steps to validate them.
- When adding features, include a minimal test and update `README.md`.

Integration and CI
- If `.github/workflows` contains CI files, follow their expectations (labels,
  required checks). If no CI exists, ask whether to add a CI workflow and which
  matrix (node versions, OS) to target.

When to stop and ask
- If any action would make non-reversible or large-scope changes (adding a
  project skeleton, converting language, deleting files), stop and ask for
  confirmation with a short plan of the intended changes.

Questions to ask the project owner (examples)
- What is the intended runtime and primary language?
- Is this a library (consumers expect semver) or an application/service?
- Which Node versions, if any, should we support? Any CI constraints?

Example quick-response template for the owner
"Repo appears empty. Do you want me to scaffold a minimal Node.js project
with `src/index.js`, `package.json`, and a basic `README.md`? If not, tell me
the intended project type and any required tooling (TypeScript, Docker, CI)."

Feedback
- If this file missed any project-specific conventions, tell me which files to
  inspect or paste key snippets (scripts, configs). I will update these
  instructions accordingly.
