"""Source locations and symbols (SPEC §6.2)."""

from dataclasses import dataclass
from enum import StrEnum

from rift.core.ids import SymbolId, module_node_id, symbol_node_id
from rift.core.models._checks import non_empty, non_negative, require
from rift.core.paths import validate_repo_path


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"
    TEST_FUNCTION = "test_function"
    TEST_METHOD = "test_method"


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    DUNDER = "dunder"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceLocation:
    """1-based lines, 0-based columns (the ``ast`` convention); end is inclusive of the line."""

    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def __post_init__(self) -> None:
        validate_repo_path(self.path, "path")
        non_negative(self.start_col, "start_col")
        non_negative(self.end_col, "end_col")
        non_negative(self.start_line - 1, "start_line - 1")
        require(self.end_line >= self.start_line, "end_line must be >= start_line")
        if self.end_line == self.start_line:
            require(self.end_col >= self.start_col, "end_col must be >= start_col on one line")


@dataclass(frozen=True, slots=True, kw_only=True)
class Symbol:
    id: SymbolId
    kind: SymbolKind
    name: str
    qualname: str
    module: str
    location: SourceLocation
    visibility: Visibility
    decorators: tuple[str, ...]

    def __post_init__(self) -> None:
        non_empty(self.name, "name")
        expected = (
            module_node_id(self.module)
            if self.kind is SymbolKind.MODULE
            else symbol_node_id(self.module, self.qualname)
        )
        require(str(self.id) == str(expected), f"symbol id {self.id!r} != {expected!r}")
        for decorator in self.decorators:
            non_empty(decorator, "decorator")
