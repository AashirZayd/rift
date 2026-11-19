# RIFT

**Proof-carrying changes for modern software.**

> **Status: pre-alpha (milestone M4: impact).** The only working commands are
> `rift --help` and `rift version`. The git layer, the core models, the static Python analyzer,
> the dependency graph and the impact engine are internal libraries with no CLI surface yet.
> Everything described under
> "What RIFT will do" is planned, not implemented. Nothing has been released to PyPI.

## What RIFT will do

RIFT is being built as a deterministic, evidence-backed change-impact and verification engine
for Python repositories. Given a code change, it is meant to answer, with traceable evidence:

1. What changed? (git)
2. What could that affect? (static analysis → dependency graph → impact)
3. Which checks are relevant? (verification planning)
4. What happened when those checks ran? (explicitly authorized execution → evidence)
5. Does the evidence satisfy policy? (PASS / FAIL / INCONCLUSIVE)
6. What don't we know? (limitations, unresolved relationships)

The output will be a **Change Proof**: a portable, schema-validated JSON document. A PASS will
mean "the policy's requirements were met by the collected evidence", never "the code is correct".

Design principles: deterministic output, conservative verdicts (unknown is never success),
local-first with no network access, and no execution of repository code unless you pass
`--execute`. RIFT is not a sandbox.

The full specification is in [`docs/SPEC.md`](docs/SPEC.md).

## Install (from source)

Requires Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and git.

```bash
git clone <this repository>
cd rift
uv sync
uv run rift version
```

## Roadmap

| Milestone | Scope | State |
|---|---|---|
| M0 | Foundation: packaging, tooling, CI, layering contracts | done |
| M1 | Core models, canonical JSON, hardened git layer | done |
| M2 | Python static analyzer | done |
| M3 | Dependency graph | done |
| M4 | Impact engine | in progress |
| M5 | Verification (pytest discovery, planning, execution) | planned |
| M6 | Change Proof protocol, evidence, policy, invariants | planned |
| M7 | Full CLI | planned |
| M8 | Plugin system | planned |
| M9 | v0.1.0 release | planned |

Current state is tracked in [`PROJECT_STATUS.md`](PROJECT_STATUS.md); design decisions in
[`docs/adr/`](docs/adr/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © RIFT contributors
