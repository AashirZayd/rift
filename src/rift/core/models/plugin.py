"""Plugin metadata (SPEC §6.4, §18). The plugin system is M8."""

from dataclasses import dataclass
from enum import StrEnum

from rift.core.models._checks import check_types, non_empty, require


class PluginKind(StrEnum):
    """One kind per protocol in ``rift.plugins.api`` (SPEC §18). Provisional (ADR-0005)."""

    LANGUAGE_ANALYZER = "language_analyzer"
    VERIFICATION_ADAPTER = "verification_adapter"
    EVIDENCE_PROVIDER = "evidence_provider"
    INTEGRATION = "integration"


@dataclass(frozen=True, slots=True, kw_only=True)
class PluginMetadata:
    name: str
    version: str
    api_version: int
    kind: PluginKind
    capabilities: tuple[str, ...]
    file_patterns: tuple[str, ...]
    requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        check_types(self)
        non_empty(self.name, "name")
        non_empty(self.version, "version")
        require(
            isinstance(self.api_version, int)
            and not isinstance(self.api_version, bool)
            and self.api_version >= 1,
            "api_version must be a positive integer",
        )
        for value in (*self.capabilities, *self.file_patterns, *self.requirements):
            non_empty(value, "entry")
