"""Benchmark (pytest-based, SPEC §4): analyze a repository and build its dependency graph.

Not part of the normal suite (``testpaths`` is ``tests``). Run it explicitly, without coverage:

    uv run pytest benchmarks --no-cov -s

``RIFT_BENCH_ROOT`` picks the repository (default: the RIFT repo itself) and
``RIFT_BENCH_REPEAT`` the number of runs (default 5). The best and median wall time of each phase
are printed. These numbers depend on the machine: they are measurements for a report, never
claims for the docs.
"""

import os
import statistics
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

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


@pytest.mark.slow
def test_build_graph_for_a_repository(capsys: pytest.CaptureFixture[str]) -> None:
    root = Path(os.environ.get("RIFT_BENCH_ROOT", str(REPO_ROOT)))
    repeat = int(os.environ.get("RIFT_BENCH_REPEAT", "5"))
    assert repeat >= 1, "RIFT_BENCH_REPEAT must be at least 1"

    timings: dict[str, list[float]] = {}
    sizes: set[tuple[int, int, int, int]] = set()
    for _ in range(repeat):
        with _timed(timings, "analyze"):
            project = analyze_project(root)
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
        sizes.add((len(project.results), graph.node_count, graph.edge_count, tests))

    (size,) = sizes  # every run builds the same graph
    files, nodes, edges, tests = size
    assert nodes > 0
    lines = [
        f"root:                  {root.name}",
        f"files analyzed:        {files}",
        f"nodes:                 {nodes} ({tests} test nodes)",
        f"edges:                 {edges}",
        f"unresolved references: {len(build.unresolved)}",
        f"dangling edges:        {len(build.dangling)}",
        f"runs:                  {repeat}",
    ]
    for phase, values in timings.items():
        best, median = min(values), statistics.median(values)
        lines.append(f"{phase:<31} best {best * 1000:9.1f} ms   median {median * 1000:9.1f} ms")
    with capsys.disabled():
        sys.stdout.write("\n" + "\n".join(lines) + "\n")
