"""Rule base class and check context."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from socc.model import SoC, Violation


@dataclass
class CheckContext:
    """Context passed to every rule during execution."""

    soc_name: str  # SoC being checked
    metadata: Dict[str, Any] = None  # additional metadata (constraints, etc.)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseRule(ABC):
    """Abstract base class; all rules must subclass this."""

    code: str = ""  # rule code, e.g. "PD-001"
    name: str = ""  # human-readable rule name
    description: str = ""  # rule description
    severity: str = "warning"  # default severity: "error", "warning", "info"

    @abstractmethod
    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Execute the rule check.

        Args:
            model: SoC data model.
            context: Execution context including metadata.

        Returns:
            List of violations found (empty list means pass).
        """
        pass

    def _create_violation(
        self,
        message: str,
        impact: str,
        suggestion: str,
        location: str,
        affected_nodes: List[str] = None,
        severity: str = None,
    ) -> Violation:
        """Create and return a Violation object for this rule.

        Args:
            message: Problem description.
            impact: System-level impact.
            suggestion: Remediation hint.
            location: Node path where the violation was found.
            affected_nodes: Related node names.
            severity: Override the rule's default severity.

        Returns:
            A populated Violation instance.
        """
        if severity is None:
            severity = self.severity

        return Violation(
            code=self.code,
            severity=severity,
            message=message,
            impact=impact,
            suggestion=suggestion,
            location=location,
            affected_nodes=affected_nodes or [],
            rule_name=self.name,
        )
