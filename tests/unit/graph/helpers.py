"""Small builders for graph tests: module nodes ``py:<name>`` and edges between them."""

from collections.abc import Iterable

from rift.core.ids import EvidenceId, NodeId
from rift.core.models.graph import Confidence, Edge, EdgeFlag, EdgeKind, EdgeOrigin, Node, NodeKind
from rift.graph.multigraph import DependencyGraph


def nid(name: str) -> NodeId:
    return NodeId(f"py:{name}")


def node(name: str) -> Node:
    return Node(id=nid(name), kind=NodeKind.MODULE, label=name, location=None)


def ev(n: int) -> EvidenceId:
    return EvidenceId(f"ev_{n:024x}")


def edge(  # noqa: PLR0913  # mirrors the Edge fields; keyword-only
    source: str,
    target: str,
    *,
    kind: EdgeKind = EdgeKind.IMPORTS,
    origin: EdgeOrigin = EdgeOrigin.NAME_RESOLUTION,
    confidence: Confidence = Confidence.CERTAIN,
    flags: Iterable[EdgeFlag] = (),
    evidence: Iterable[int] = (),
) -> Edge:
    return Edge(
        source=nid(source),
        target=nid(target),
        kind=kind,
        origin=origin,
        confidence=confidence,
        evidence_ids=tuple(sorted(ev(n) for n in set(evidence))),
        flags=frozenset(flags),
    )


def graph_of(names: Iterable[str], edges: Iterable[Edge] = ()) -> DependencyGraph:
    graph = DependencyGraph()
    for name in names:
        graph.add_node(node(name))
    for item in edges:
        graph.add_edge(item)
    return graph
