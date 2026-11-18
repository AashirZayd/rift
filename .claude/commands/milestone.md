---
description: Start a RIFT milestone (e.g. /milestone M1)
argument-hint: M<n>
---

Start milestone **$ARGUMENTS** for RIFT.

1. Read `PROJECT_STATUS.md`. If the previous milestone is not marked "accepted" by the user, stop and say so.
2. Read `docs/SPEC.md` §24 for $ARGUMENTS acceptance criteria, plus every spec section that milestone touches. Read relevant ADRs in `docs/adr/`.
3. Write a design note (≤ 1 page) in your reply: modules touched, public functions/types, data flow, layer placement, test plan mapped to each acceptance criterion, risks, decisions needing ADRs. Then proceed — don't wait for approval of the design note unless a decision is irreversible or contradicts the spec.
4. Create a branch `m<n>/<topic>` and a task list mirroring the acceptance criteria.
5. Implement the smallest complete vertical slice first, then widen. Commit in small steps.
6. Do not implement anything belonging to a later milestone. If you need a stub, make it raise `NotImplementedError` with the milestone name and note it in PROJECT_STATUS.
7. Finish with `/verify`, update `PROJECT_STATUS.md`, then `/report`. Stop and wait for review.
