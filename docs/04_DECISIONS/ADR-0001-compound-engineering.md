# ADR-0001: Compound Engineering Workflow

## Status
Accepted

## Context
The codebase was growing without a standardized context map or automated quality gates. New agents or developers had to reverse-engineer the architecture, leading to potential regressions and high cognitive load.

## Decision
We are adopting a "Compound Engineering" approach:
1.  **Durable Context**: Maintaining live documentation (`docs/`) that maps the codebase for both humans and agents.
2.  **Quality Gates**: Enforcing linting, type-checking, and testing on every change (Phase 2).
3.  **Traceability**: Using Artifacts for every major task (Plan, Execution, Validation).

## Consequences
*   **Positive**:
    *   Future agents can onboard instantly by reading `docs/03_AGENT_PLAYBOOK.md`.
    *   Regressions in critical paths (Risk, Auth) are caught by CI/CD.
    *   Design decisions are logged.
*   **Negative**:
    *   Slight overhead in updating docs when architecture changes.
    *   Must maintain CI configuration.

## Implementation
*   Docs structure created in Phase 1.
*   CI/CD workflows to be added in Phase 2.
