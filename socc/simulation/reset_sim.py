"""Reset line state machine simulation.

Simulates hardware reset deassertion ordering during boot and detects
reset-sequencing violations (RS-001, RS-002).
"""

from __future__ import annotations

import fnmatch
from typing import Dict, List, Optional, Tuple

from socc.model.base import IRNode
from socc.simulation.types import ResetState, SimEvent, SimViolation


class ResetStateMachine:
    """Behavioural model of hardware reset lines during SoC boot.

    In a real SoC the CRU (Clock Reset Unit) must be fully initialised
    before peripheral resets can safely be deasserted.  This state machine
    verifies the ordering requirements declared in the SoC YAML.
    """

    def __init__(
        self,
        reset_dependencies: Optional[List[dict]] = None,
        required_resets_patterns: Optional[List[dict]] = None,
    ):
        """
        Args:
            reset_dependencies: list of dicts with:
                  device_pattern (glob): device node name suffix to match
                  requires_before_deassert: list of provider names that must
                      be deasserted/ready first (e.g. ["cru", "phy"])
            required_resets_patterns: list of dicts with:
                  device_pattern (glob)
                  required (bool): whether a 'resets' property is mandatory
        """
        self.reset_dependencies: List[dict] = reset_dependencies or []
        self.required_resets_patterns: List[dict] = required_resets_patterns or []

        self.states: Dict[str, ResetState] = {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _matches(self, device_path: str, pattern: str) -> bool:
        """True if *device_path* contains the glob *pattern*."""
        return fnmatch.fnmatch(device_path, f"*{pattern}*")

    def _requires_before_deassert(self, device_path: str) -> List[str]:
        """Return list of provider names that must be ready before *device_path*."""
        for dep in self.reset_dependencies:
            pattern = dep.get("device_pattern", "")
            if self._matches(device_path, pattern):
                return dep.get("requires_before_deassert", [])
        return []

    def _reset_required(self, device_path: str) -> bool:
        """Return True if *device_path* must have a 'resets' property."""
        for pat in self.required_resets_patterns:
            if self._matches(device_path, pat.get("device_pattern", "")):
                return bool(pat.get("required", False))
        return False

    # ── Boot simulation ───────────────────────────────────────────────────────

    def simulate_boot_deassert(
        self,
        devices: Dict[str, IRNode],
        provider_ready: Optional[List[str]] = None,
        current_time: float = 0.0,
    ) -> Tuple[List[SimEvent], List[SimViolation]]:
        """Simulate reset deassertion during SoC boot.

        RS-001: A device's reset is deasserted before the required clock /
                reset provider (e.g. CRU) is ready.

        Args:
            devices: mapping device_name → IRNode from the SoC model.
            provider_ready: list of provider names that are considered ready
                (e.g. ["cru"]).  Defaults to an empty list.
            current_time: starting simulation time in milliseconds.

        Returns:
            (events, violations)
        """
        if provider_ready is None:
            provider_ready = []

        ready_set = set(name.lower() for name in provider_ready)
        events: List[SimEvent] = []
        violations: List[SimViolation] = []

        for dev_name, node in devices.items():
            path = node.path or dev_name
            required_providers = self._requires_before_deassert(path)

            # Check each required provider
            for provider in required_providers:
                if provider.lower() not in ready_set:
                    violations.append(SimViolation(
                        code="RS-001",
                        severity="error",
                        message=(
                            f"Device '{dev_name}' reset deasserted before "
                            f"required provider '{provider}' is ready"
                        ),
                        time_ms=current_time,
                        scenario="boot",
                        component=dev_name,
                        detail=(
                            f"Device path: {path}. "
                            f"Provider '{provider}' is not in the ready set: "
                            f"{sorted(ready_set) or ['(empty)']}"
                        ),
                        suggestion=(
                            f"Ensure '{provider}' is fully initialised before "
                            f"'{dev_name}' is probed. Add a deferred-probe or "
                            f"check the reset provider binding in DTS."
                        ),
                    ))

            # Record deassert event
            self.states[dev_name] = ResetState.DEASSERTED
            events.append(SimEvent(
                time_ms=current_time,
                component=dev_name,
                component_type="reset",
                old_state=ResetState.ASSERTED.value,
                new_state=ResetState.DEASSERTED.value,
            ))
            current_time += 0.1  # negligible inter-device gap

        return events, violations

    # ── Missing resets check ──────────────────────────────────────────────────

    def check_missing_resets(
        self,
        devices: Dict[str, IRNode],
    ) -> List[SimViolation]:
        """Detect RS-002: devices that require a 'resets' property but lack one.

        RS-002 is flagged when a device matches a required_resets_pattern but
        its IRNode does not have a 'resets' property.  A missing resets
        property means the driver cannot perform a controlled warm-reboot
        reset.

        Returns:
            List of RS-002 violations.
        """
        violations: List[SimViolation] = []

        for dev_name, node in devices.items():
            path = node.path or dev_name
            if not self._reset_required(path):
                continue

            has_resets = node.has_property("resets")
            if not has_resets:
                violations.append(SimViolation(
                    code="RS-002",
                    severity="warning",
                    message=(
                        f"Device '{dev_name}' missing required "
                        f"'resets' property"
                    ),
                    time_ms=0.0,
                    scenario="boot",
                    component=dev_name,
                    detail=(
                        f"Device path: {path}. "
                        f"SoC database requires a resets property for "
                        f"devices matching this peripheral type."
                    ),
                    suggestion=(
                        f"Add 'resets = <&cru SRST_*>;' to '{dev_name}' "
                        f"in the DTS file. Without it the driver cannot "
                        f"perform a safe warm reset or driver unbind."
                    ),
                ))

        return violations
