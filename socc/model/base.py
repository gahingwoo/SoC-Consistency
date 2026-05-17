"""Core data structures for the SoC IR model."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IRNode:
    """Internal representation of a device-tree node."""

    name: str  # node name (e.g., "i2c0")
    path: str  # full path (e.g., "/soc/i2c@fac0000")
    properties: Dict[str, Any] = field(default_factory=dict)
    children: List["IRNode"] = field(default_factory=list)
    parent: Optional["IRNode"] = None
    source_line: Optional[int] = None    # line number of the opening '{' in the DTS file
    source_file: Optional[str] = None   # source DTS file path (set by parser)

    def get_property(self, key: str, default: Any = None) -> Any:
        """Return property value, or *default* if not present."""
        return self.properties.get(key, default)

    def has_property(self, key: str) -> bool:
        """Return True if the property exists."""
        return key in self.properties

    def get_phandle_refs(self) -> List[str]:
        """Return all phandle references (e.g. &regulator, &clk)."""
        refs = []
        for value in self.properties.values():
            if isinstance(value, str) and value.startswith("&"):
                refs.append(value[1:])  # strip ''' prefix
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.startswith("&"):
                        refs.append(item[1:])
        return refs

    def find_child(self, name: str) -> Optional["IRNode"]:
        """Return the direct child with the given name, or None."""
        for child in self.children:
            if child.name == name:
                return child
        return None

    def find_node_by_path(self, path: str) -> Optional["IRNode"]:
        """Depth-first search for a node by its full path."""
        if self.path == path:
            return self
        for child in self.children:
            result = child.find_node_by_path(path)
            if result:
                return result
        return None


@dataclass
class Violation:
    """A single rule violation reported by a rule check."""

    code: str  # rule code (e.g., "PD-001")
    severity: str  # "error", "warning", "info"
    message: str  # problem description
    impact: str  # impact on the system
    suggestion: str  # remediation hint
    location: str  # node path
    affected_nodes: List[str] = field(default_factory=list)
    rule_name: str = ""
    reference: str = ""
    line: Optional[int] = None        # source line number in the DTS file
    source_file: Optional[str] = None # path to the DTS source file (for diagnostics)

    def __str__(self) -> str:
        """Human-readable single-violation display."""
        tag = {"error": "[ERROR]", "warning": "[WARNING]", "info": "[INFO]"}.get(
            self.severity, "[?]"
        )
        loc = f"{self.location}" + (f":{self.line}" if self.line else "")
        return (
            f"{tag} [{self.code}] {self.rule_name}\n"
            f"  Location : {loc}\n"
            f"  Issue    : {self.message}\n"
            f"  Impact   : {self.impact}\n"
            f"  Fix      : {self.suggestion}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary (used for JSON output)."""
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "impact": self.impact,
            "suggestion": self.suggestion,
            "location": self.location,
            "affected_nodes": self.affected_nodes,
            "rule_name": self.rule_name,
            "reference": self.reference,
            "line": self.line,
            "source_file": self.source_file,
        }

@dataclass
class Device:
    """A parsed device node extracted from the device tree."""
    name: str
    node_path: str
    compatible: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # noqa: D105
        return f"Device({self.name}@{self.node_path})"