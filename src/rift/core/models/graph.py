"""Dependency graph vocabulary (SPEC §6.3). The graph itself is M3; these are the value types."""

from dataclasses import dataclass, field
from enum import StrEnum

from rift.core.frozen import FrozenMap
from rift.core.ids import EvidenceId, NodeId, validate_evidence_id, validate_node_id
from rift.core.models._checks import check_types, non_empty, require, sorted_unique
from rift.core.models.source import SourceLocation


class NodeKind(StrEnum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    TEST = "test"
    API_CONTRACT = "api_contract"


class EdgeKind(StrEnum):
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    REFERENCES = "references"
    TESTS = "tests"
    DEPENDS_ON = "depends_on"


class EdgeOrigin(StrEnum):
    AST_STATIC = "ast_static"
    NAME_RESOLUTION = "name_resolution"
    HEURISTIC = "heuristic"
    CONFIG_DECLARED = "config_declared"
    PLUGIN = "plugin"


_CONFIDENCE_RANK = {"unknown": 0, "possible": 1, "likely": 2, "certain": 3}


class Confidence(StrEnum):
    """Ordinal confidence (never a float). Compares by strength: certain > likely > ..."""

    CERTAIN = "certain"
    LIKELY = "likely"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return _CONFIDENCE_RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.rank >= other.rank


class EdgeFlag(StrEnum):
    TYPE_CHECKING_ONLY = "type_checking_only"
    CONDITIONAL = "conditional"
    STAR_IMPORT = "star_import"
    DYNAMIC = "dynamic"


class UnresolvedReason(StrEnum):
    DYNAMIC_IMPORT = "dynamic_import"
    DYNAMIC_ATTRIBUTE = "dynamic_attribute"
    STAR_IMPORT = "star_import"
    EXTERNAL_MODULE = "external_module"
    MISSING_MODULE = "missing_module"
    SYNTAX_ERROR = "syntax_error"
    EXEC_EVAL = "exec_eval"
    SYS_PATH_MUTATION = "sys_path_mutation"
    MODULE_GETATTR = "module_getattr"
    UNKNOWN = "unknown"


def _check_node_kind(node_id: NodeId, kind: NodeKind) -> None:
    """The ID prefix must agree with the node kind (ADR-0005)."""
    prefix, _, rest = node_id.partition(":")
    if kind is NodeKind.FILE:
        require(prefix == "file", f"{kind} node needs a file: id, got {node_id!r}")
    elif kind is NodeKind.TEST:
        require(prefix == "test", f"{kind} node needs a test: id, got {node_id!r}")
    elif kind is NodeKind.MODULE:
        require(prefix == "py" and ":" not in rest, f"module node needs py:<module>: {node_id!r}")
    elif kind in {NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD}:
        require(prefix == "py" and ":" in rest, f"{kind} node needs py:<module>:<qualname>")


@dataclass(frozen=True, slots=True, kw_only=True)
class Node:
    id: NodeId
    kind: NodeKind
    label: str
    location: SourceLocation | None
    attributes: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        check_types(self)
        validate_node_id(self.id)
        _check_node_kind(self.id, self.kind)
        non_empty(self.label, "label")


@dataclass(frozen=True, slots=True, kw_only=True)
class Edge:
    source: NodeId
    target: NodeId
    kind: EdgeKind
    origin: EdgeOrigin
    confidence: Confidence
    evidence_ids: tuple[EvidenceId, ...]
    flags: frozenset[EdgeFlag]

    def __post_init__(self) -> None:
        check_types(self)
        validate_node_id(self.source)
        validate_node_id(self.target)
        for evidence_id in self.evidence_ids:
            validate_evidence_id(evidence_id)
        sorted_unique(self.evidence_ids, "evidence_ids")
        require(all(isinstance(flag, EdgeFlag) for flag in self.flags), "flags must be EdgeFlag")


@dataclass(frozen=True, slots=True, kw_only=True)
class UnresolvedReference:
    source: NodeId
    raw: str
    reason: UnresolvedReason
    location: SourceLocation

    def __post_init__(self) -> None:
        check_types(self)
        validate_node_id(self.source)
        non_empty(self.raw, "raw")
