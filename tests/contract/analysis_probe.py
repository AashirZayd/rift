"""Subprocess probe for tests/contract/test_determinism.py (not collected: no test_ prefix).

Usage: python analysis_probe.py <project-root>

Prints one JSON object: ``output`` holds the canonical JSON of every AnalysisResult of the
project; ``set_order`` is a raw set iteration, which must differ across PYTHONHASHSEED values.
"""

import json
import sys
from pathlib import Path

from rift.analysis.python.analyzer import analyze_project
from rift.core.canonical import canonical_json
from rift.core.serialization import to_dict

WORDS = {f"word-{i:03d}" for i in range(64)}


def main(root: str) -> None:
    project = analyze_project(Path(root))
    output = [canonical_json(to_dict(result)) for result in project.results]
    output.append(canonical_json([to_dict(lim) for lim in project.limitations]))
    output.append(canonical_json(list(project.source_roots)))
    sys.stdout.write(json.dumps({"output": output, "set_order": list(WORDS)}))


if __name__ == "__main__":
    main(sys.argv[1])
