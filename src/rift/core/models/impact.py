"""Impact results (SPEC §6.4, §11): the value types; ``rift.impact`` computes them."""

from dataclasses import dataclass
from enum import StrEnum

from rift.core.ids import NodeId, validate_node_id
from rift.core.models._checks import check_types, require, sorted_unique, strictly_sorted
from rift.core.models.analysis import limitation_sort_key, unresolved_sort_key
from rift.core.models.evidence import Limitation
from rift.core.models.graph import UnresolvedReference

# Limitation kinds that inform without affecting completeness (ADR-0010 §9).
INFORMATIONAL_LIMITATION_KINDS = frozenset({"dynamic_code_in_project"})


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
        check_types(self)
        validate_node_id(self.node)
        require(len(self.path) >= 1, "path must not be empty")
        for step in self.path:
            validate_node_id(step)
        require(self.path[-1] == self.node, "path must end at node")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactResult:
    """What a change could affect (SPEC §11).

    ``classification`` is an ordinal tier, never a risk probability. ``completeness`` is
    orthogonal to it: ``limitations`` lists every reason the result is not complete (a partial or
    unknown result always has at least one), plus informational entries that do not affect
    completeness (e.g. project-wide dynamic imports, ADR-0010 §9). Every affected node and related
    test has an explanation path from a changed node.
    """

    changed_nodes: tuple[NodeId, ...]
    affected_nodes: tuple[NodeId, ...]
    public_interfaces: tuple[NodeId, ...]
    related_tests: tuple[NodeId, ...]
    unresolved: tuple[UnresolvedReference, ...]
    classification: ImpactClass
    completeness: Completeness
    limitations: tuple[Limitation, ...]
    explanations: tuple[ImpactExplanation, ...]

    def __post_init__(self) -> None:
        check_types(self)
        for field_name in ("changed_nodes", "affected_nodes", "public_interfaces", "related_tests"):
            values: tuple[NodeId, ...] = getattr(self, field_name)
            for node_id in values:
                validate_node_id(node_id)
            sorted_unique(values, field_name)
        require(
            all(node.startswith("test:") for node in self.related_tests),
            "related_tests must be test: nodes",
        )
        strictly_sorted(self.unresolved, unresolved_sort_key, "unresolved")
        strictly_sorted(self.limitations, limitation_sort_key, "limitations")
        reasons = [
            lim for lim in self.limitations if lim.kind not in INFORMATIONAL_LIMITATION_KINDS
        ]
        if self.completeness is Completeness.COMPLETE:
            require(not reasons, "a complete result lists informational limitations only")
        else:
            require(bool(reasons), "a partial or unknown result must list at least one reason")
        changed, affected = set(self.changed_nodes), set(self.affected_nodes)
        require(not changed & affected, "a node is either changed or affected, not both")
        impact_set = changed | affected
        require(set(self.related_tests) <= impact_set, "related tests must be changed or affected")
        require(
            set(self.public_interfaces) <= impact_set,
            "public interfaces must be changed or affected",
        )
        require(
            (self.classification is ImpactClass.NONE) == (not self.changed_nodes),
            "classification is none exactly when nothing changed",
        )
        explained = [explanation.node for explanation in self.explanations]
        sorted_unique(explained, "explanations")
        require(
            affected | set(self.related_tests) <= set(explained),
            "every affected node and related test needs an explanation",
        )
        require(
            all(explanation.path[0] in changed for explanation in self.explanations),
            "an explanation path starts at a changed node",
        )
