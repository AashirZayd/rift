# ADR-0001: Record architecture decisions

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M0
- **Spec refs:** §29, §30

## Context

RIFT is built milestone by milestone, often across interrupted sessions. The spec deliberately
leaves some questions open (§30) and requires that every ambiguity be resolved with the smallest
defensible choice and recorded (§29). Decisions that live only in chat or commit messages get lost
and get re-litigated.

## Decision

Record every non-trivial architectural decision as an ADR in `docs/adr/NNNN-kebab-title.md`,
using `0000-template.md`. Numbers are sequential and never reused. An ADR is changed only to fix
typos or to update its status. A decision is reversed by a new ADR that supersedes it.

An ADR is required for: anything §29 or §30 asks to be decided; any new runtime dependency (§3.1);
any schema or protocol change (§16); any deviation from a suggestion in the spec.

Each ADR names the test or command that pins the decision where one exists.

## Alternatives considered

- **Decisions in PROJECT_STATUS.md only.** That file is mutable state, not history.
- **Decisions in commit messages only.** Hard to find, impossible to supersede cleanly.

## Consequences

Precedence when sources conflict: SPEC > ADRs > CLAUDE.md. `PROJECT_STATUS.md` keeps an ADR index.
