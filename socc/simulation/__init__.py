"""Physical simulation layer for SoC device-tree validation.

Provides behavioural-level simulation of power sequencing, clock gating,
and reset ordering to catch dynamic bugs that static rule analysis cannot detect.

Violation codes introduced in v1.3.0
────────────────────────────────────
  PS-001  Power supply not stable before consumer probes (boot)
  PS-002  Supply disabled before consumer device finishes suspend
  PS-003  Child regulator enabled before parent is stable (boot order)
  CG-001  Clock gated (power domain off) while consumer device still active
  CG-002  Parent clock disabled while child consumers still enabled
  RS-001  Device reset deasserted before its clock provider is ready
  RS-002  Device missing required 'resets' property (warm-reboot risk)
"""

from .types import (
    RegulatorState,
    ClockState,
    ResetState,
    DeviceState,
    SimEvent,
    SimViolation,
    ScenarioResult,
)
from .power_sim import PowerStateMachine
from .clock_sim import ClockStateMachine
from .reset_sim import ResetStateMachine
from .runner import ScenarioRunner

__all__ = [
    "RegulatorState",
    "ClockState",
    "ResetState",
    "DeviceState",
    "SimEvent",
    "SimViolation",
    "ScenarioResult",
    "PowerStateMachine",
    "ClockStateMachine",
    "ResetStateMachine",
    "ScenarioRunner",
]
