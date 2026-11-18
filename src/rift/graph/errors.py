"""Graph errors: structural violations raised by :class:`rift.graph.DependencyGraph`."""

from rift.core.errors import ErrorCode, RiftError


class GraphError(RiftError):
    """An operation referenced a node the graph does not hold, or contradicted it."""

    default_code = ErrorCode.UNKNOWN_NODE
