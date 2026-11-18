"""Impact results (SPEC §6.4, §11). Computing impact is M4; these are the value types."""

from dataclasses import dataclass
from enum import StrEnum

from rift.core.ids import NodeId, validate_node_id
from rift.core.models._checks import require, sorted_unique
from rift.core.models.graph import UnresolvedReference


class ImpactClass(StrEnum):
    """Ordinal impact tier (SPEC §11.1). Not a risk probability."""

    NONE = "none"
    LOCAL = "local"
    MODULE = "module"
    CROSS_PACKAGE = "cross_package"
    PUBLIC_API = "public_api"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactExplanation:
    """Why ``node`` is affected: the shortest path from a changed node, ending at ``node``."""

    node: NodeId
    path: tuple[NodeId, ...]

    def __post_init__(self) -> None:
        validate_node_id(self.node)
        require(len(self.path) >= 1, "path must not be empty")
        for step in self.path:
            validate_node_id(step)
        require(self.path[-1] == self.node, "path must end at node")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactResult:
    changed_nodes: tuple[NodeId, ...]
    affected_nodes: tuple[NodeId, ...]
    public_interfaces: tuple[NodeId, ...]
    related_tests: tuple[NodeId, ...]
    unresolved: tuple[UnresolvedReference, ...]
    classification: ImpactClass
    completeness: Completeness
    explanations: tuple[ImpactExplanation, ...]

    def __post_init__(self) -> None:
        for field_name in ("changed_nodes", "affected_nodes", "public_interfaces", "related_tests"):
            values: tuple[NodeId, ...] = getattr(self, field_name)
            for node_id in values:
                validate_node_id(node_id)
            sorted_unique(values, field_name)
        sorted_unique([explanation.node for explanation in self.explanations], "explanations")
