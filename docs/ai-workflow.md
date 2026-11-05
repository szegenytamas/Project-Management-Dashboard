# AI-assisted workflow

This project was built with **Claude Code** as a pair programmer and mentor. This file
documents how: what was delegated, what I reviewed or verified myself, what I decided or
changed, and why. The conventions the AI works under are in [`CLAUDE.md`](../CLAUDE.md).

> Written by me (the developer) after each phase, in my own words.

## How I work with the AI

- Claude Code writes code and explains each step; **I run all git commands myself**
  (branches, commits, pushes, pull requests) and review every change before committing.
- Every phase ends with passing checks (`uv run tox`) and a green CI run before I merge.
- After each phase I study a generated study guide and a decision document
  (alternatives and trade-offs) so I can explain every part of the system.

### Skills and tools used

| Skill / tool   | Used for                                                     |
| -------------- | ------------------------------------------------------------ |
| PDF skill      | Study guides and the technology-decisions document per phase |
| Artifacts      | An interactive flowchart page of every application flow      |
| `/code-review` | <!-- e.g. reviewing each PR before merging -->               |

---

## Phase 0 – Repository and conventions

- **Delegated:** I have asked the AI to plan the design and agree on a roadmap we will follow to generate a seamless workflow. For example version control and API structure, testing.
- **Reviewed / verified myself:** I review code before commits and changes.
- **Decided or changed:** I have decided on doing the git actions by myself so I can be in control what gets pushed into the repo.

## Phase 1 – Project skeleton

- **Delegated:** I have asked for a project skeleton from Claude, like project structure, the tools that check quality, a minimal running web service, the container that ships it, and CI that checks every change.
- **Reviewed / verified myself:** I have reviewed the work and verified that the small web service is healthy and running.
- **Decided or changed:** In this phase I did not need to change or decide on anything.

## Phase 2 – Database layer

- **Delegated:** I have asked AI to setup a database layer, to create the required tables for the project, set constraints, utilize normalization, also include Alembic for migrations
- **Reviewed / verified myself:** Handled git commands myself and also merging, verified that CI checks are green.
- **Decided or changed:** Everything checked good, nothing to decide or change.

## Phase 3 – Authentication

- **Delegated:** I delegated AI to solve authentication and authorization problem for the task, registration and input validation, password hashing and salting, protect endpoints.
- **Reviewed / verified myself:** Like at every phase I handle git and review and check the application that its working.
- **Decided or changed:** Decided to have stricter password constraints at registrations.

## Phase 4 – Projects, invitations, permissions

- **Delegated:**
- **Reviewed / verified myself:** I caught a failing smoke test in CI and brought it back, and the fix included making the next failure easier to diagnose
- **Decided or changed:**
