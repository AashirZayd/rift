---
description: Produce the milestone completion report and update PROJECT_STATUS.md
---

1. Make sure `/verify` was run after the last code change. If not, run it now.
2. Update `PROJECT_STATUS.md` (format in `docs/SPEC.md` §27). Only list items as "Completed (verified)" with the test or command that verifies them. Set milestone state to "awaiting review".
3. Output the completion report using the template in `CLAUDE.md`, with real command output numbers. List anything unverified explicitly (e.g. CI not run on GitHub).
4. Self-review checklist — answer each yes/no with a one-line reason:
   - Any layer violations or new cross-module imports outside §4.1?
   - Any non-deterministic output (unsorted collections, set iteration, wall-clock in IDs, absolute paths)?
   - Any subprocess not using argv lists / any repo code executed in a passive path?
   - Any new runtime dependency?
   - Any schema/protocol change without ADR?
   - Anything from a future milestone implemented?
5. Stop. Do not begin the next milestone.
