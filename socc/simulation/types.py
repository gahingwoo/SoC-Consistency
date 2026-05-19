"""Core data types for the simulation layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# State enumerations
# ─────────────────────────────────────────────────────────────────────────────

class RegulatorState(Enum):
    """Lifecycle states of a power regulator."""
    OFF        = "off"
    RAMPING_UP = "ramping_up"
    ON         = "on"
    SUSPENDING = "suspending"
    SUSPENDED  = "suspended"


class ClockState(Enum):
    """Lifecycle states of a clock signal."""
    DISABLED  = "disabled"
    ENABLING  = "enabling"
    ENABLED   = "enabled"
    DISABLING = "disabling"


class ResetState(Enum):
    """Lifecycle states of a hardware reset line."""
    ASSERTED    = "asserted"      # peripheral held in reset
    DEASSERTING = "deasserting"   # reset being released
    DEASSERTED  = "deasserted"    # peripheral running


class DeviceState(Enum):
    """Lifecycle states of a device during PM transitions."""
    OFFLINE    = "offline"
    PROBING    = "probing"
    ACTIVE     = "active"
    SUSPENDING = "suspending"
    SUSPENDED  = "suspended"


# ─────────────────────────────────────────────────────────────────────────────
# Event record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimEvent:
    """A single timestamped state transition recorded during simulation."""

    time_ms: float
    """Wall-clock time within the simulated scenario (milliseconds)."""

    component: str
    """Name of the component that changed state."""

    component_type: str
    """One of: 'regulator', 'clock', 'reset', 'device'."""

    old_state: str
    new_state: str

    triggered_by: Optional[str] = None
    """Name of the component or scenario step that caused this transition."""


# ─────────────────────────────────────────────────────────────────────────────
# Violation record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimViolation:
    """A constraint violation detected by the simulation engine."""

    code: str
    """Rule code, e.g. 'PS-001'."""

    severity: str
    """'error' or 'warning'."""

    message: str
    """One-line human-readable description."""

    time_ms: float
    """Simulation time at which the violation was detected (ms)."""

    scenario: str
    """Scenario name: 'boot', 'suspend', 'resume', 'runtime_pm'."""

    component: str
    """Primary component involved (regulator, clock, or device name)."""

    detail: str
    """Extended technical detail."""

    suggestion: str
    """Actionable fix suggestion."""


# ─────────────────────────────────────────────────────────────────────────────
# Scenario result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    """Aggregated result of a simulation scenario run."""

    scenario: str
    violations: List[SimViolation] = field(default_factory=list)
    timeline: List[SimEvent] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def is_safe(self) -> bool:
        """True when no error-severity violations were found."""
        return not any(v.severity == "error" for v in self.violations)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")
