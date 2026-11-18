"""Subprocess probe for tests/contract/test_determinism.py (not collected: no test_ prefix).

Usage: python graph_probe.py <project-root>

Analyzes the project, builds the dependency graph, and prints one JSON object: ``output`` holds
the canonical JSON of the graph, its unresolved references, and forward and reverse reachability
(nodes, depths and explanation paths) from every node; ``set_order`` is a raw set iteration,
which must differ across PYTHONHASHSEED values.
"""

import json
import sys
from pathlib import Path

from rift.analysis.python.analyzer import analyze_project
from rift.core.canonical import canonical_json
from rift.core.serialization import to_dict
from rift.graph.builder import build_graph
from rift.graph.traversal import Reachability

WORDS = {f"word-{i:03d}" for i in range(64)}


def _explain(result: Reachability) -> list[object]:
    return [
        [
            node,
            result.depth(node),
            list(result.path(node)),
            [e.kind.value for e in result.path_edges(node)],
        ]
        for node in result.nodes
    ]


def main(root: str) -> None:
    build = build_graph(analyze_project(Path(root)).results)
    graph = build.graph
    output = [
        canonical_json(graph.to_dict()),
        canonical_json([to_dict(ref) for ref in build.unresolved]),
    ]
    for node in graph.nodes():
        output.append(canonical_json(_explain(graph.forward_reachable(node.id))))
        output.append(canonical_json(_explain(graph.reverse_reachable(node.id))))
    sys.stdout.write(json.dumps({"output": output, "set_order": list(WORDS)}))


if __name__ == "__main__":
    main(sys.argv[1])
