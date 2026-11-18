"""Benchmark: analyze a repository and build its dependency graph (default: the RIFT repo itself).

Usage: uv run python benchmarks/graph_build.py [repo-root] [--repeat N]

Prints the graph size and the best and median wall time of each phase over N runs. These
numbers depend on the machine; they are measurements for a report, not claims for the docs.
"""

import argparse
import statistics
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rift.analysis.python.analyzer import analyze_project
from rift.core.canonical import canonical_json
from rift.core.models.graph import NodeKind
from rift.graph.builder import build_graph

REPO_ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def _timed(timings: dict[str, list[float]], phase: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    timings.setdefault(phase, []).append(time.perf_counter() - start)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", type=Path, default=REPO_ROOT)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()

    timings: dict[str, list[float]] = {}
    for _ in range(args.repeat):
        with _timed(timings, "analyze"):
            project = analyze_project(args.root)
        with _timed(timings, "build_graph"):
            build = build_graph(project.results)
        graph = build.graph
        with _timed(timings, "to_json"):
            canonical_json(graph.to_dict())
        targets = [n.id for n in graph.nodes() if n.kind is not NodeKind.FILE]
        with _timed(timings, "reverse_reachable(each symbol)"):
            for node_id in targets:
                graph.reverse_reachable(node_id)

    tests = sum(1 for n in graph.nodes() if n.kind is NodeKind.TEST)
    lines = [
        f"files analyzed:        {len(project.results)}",
        f"nodes:                 {graph.node_count} ({tests} test nodes)",
        f"edges:                 {graph.edge_count}",
        f"unresolved references: {len(build.unresolved)}",
        f"traversal starts:      {len(targets)}",
        f"runs:                  {args.repeat}",
    ]
    for phase, values in timings.items():
        best, median = min(values), statistics.median(values)
        lines.append(f"{phase:<31} best {best * 1000:9.1f} ms   median {median * 1000:9.1f} ms")
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
