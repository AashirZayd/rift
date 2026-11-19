"""Subprocess probe for tests/contract/test_determinism.py (not collected: no test_ prefix).

Usage: python impact_probe.py <repo-root> <base>

Computes the impact of base → working tree (git → analysis → graph → ImpactResult) and prints
one JSON object: ``output`` holds the canonical JSON of the ChangeSet and the ImpactResult;
``set_order`` is a raw set iteration, which must differ across PYTHONHASHSEED values.
"""

import json
import sys
from pathlib import Path

from rift.core.canonical import canonical_json
from rift.core.serialization import to_dict
from rift.services.impact import impact_of_changes

WORDS = {f"word-{i:03d}" for i in range(64)}


def main(root: str, base: str) -> None:
    outcome = impact_of_changes(Path(root), base=base)
    output = [canonical_json(to_dict(outcome.changes)), canonical_json(to_dict(outcome.impact))]
    sys.stdout.write(json.dumps({"output": output, "set_order": list(WORDS)}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
