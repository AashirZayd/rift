# Security Policy

> Stub (M0). The full threat model is specified in `docs/SPEC.md` §21 and will be documented here
> as the controls are implemented and tested.

## Supported versions

RIFT is pre-alpha and has no released versions. Only the `main` branch receives fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately through **GitHub private vulnerability reporting**: open the repository's
**Security** tab and choose **Report a vulnerability**. Only the maintainers can see the report.

Include the affected version or commit, a description of the issue, and steps to reproduce.
We aim to acknowledge reports within 7 days.

## Scope notes

- RIFT is designed so that passive commands never execute code from the analyzed repository.
  A way to make a passive command execute repository-controlled code is a security bug.
- `--execute` runs the repository's tests with your user's privileges. **RIFT is not a sandbox**
  and does not isolate executed tests.
- Allowed plugins run with full user privileges; the allowlist is a trust decision, not isolation.
