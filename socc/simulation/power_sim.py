"""Power domain state machine simulation.

Simulates regulator enable/disable sequences and detects power-sequencing
violations (PS-001, PS-002, PS-003).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from socc.model.power import PowerTree
from socc.simulation.types import (
    RegulatorState, DeviceState, SimEvent, SimViolation
)


class PowerStateMachine:
    """Behavioural model of a SoC power tree across boot / suspend / resume.

    All times are in milliseconds.
    """

    def __init__(
        self,
        power_tree: PowerTree,
        stability_requirements: Optional[Dict[str, float]] = None,
    ):
        """
        Args:
            power_tree: The SoC power tree (Regulator nodes + edges).
            stability_requirements: mapping of regulator_name →
                required_stable_ms.  Consumers must not probe until their
                supply has been stable for at least this long.
        """
        self.power_tree = power_tree
        self.stability_requirements: Dict[str, float] = stability_requirements or {}

        # Current state of each regulator
        self.states: Dict[str, RegulatorState] = {
            name: RegulatorState.OFF for name in power_tree.nodes
        }
        # Time at which each regulator became fully ON/stable
        self.stable_at: Dict[str, float] = {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _topo_order(self) -> List[str]:
        """Return regulator names in topological order (root first, leaves last)."""
        visited: Set[str] = set()
        order: List[str] = []

        def _dfs(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            reg = self.power_tree.nodes.get(name)
            if reg and reg.parent and reg.parent in self.power_tree.nodes:
                _dfs(reg.parent)
            order.append(name)

        for name in self.power_tree.nodes:
            _dfs(name)
        return order

    def _rev_topo_order(self) -> List[str]:
        """Return regulator names in reverse topological order (leaves first)."""
        return list(reversed(self._topo_order()))

    def reset_to_boot(self) -> None:
        """Reset all states back to OFF (used before boot simulation)."""
        for name in self.states:
            self.states[name] = RegulatorState.OFF
        self.stable_at.clear()

    def reset_to_on(self) -> None:
        """Set all states to ON at t=0 (used before suspend simulation)."""
        for name in self.states:
            self.states[name] = RegulatorState.ON
            self.stable_at[name] = 0.0

    # ── Boot simulation ───────────────────────────────────────────────────────

    def simulate_boot(
        self,
        device_supplies: Dict[str, List[str]],
    ) -> Tuple[List[SimEvent], List[SimViolation]]:
        """Simulate power-on boot sequence.

        Returns:
            (events, violations) where violations may contain PS-001 and PS-003.

        Violation PS-001:
            A consumer device's supply does not meet the required stability
            window before the device would probe.

        Violation PS-003:
            A child regulator is enabled before its parent is fully stable.
        """
        self.reset_to_boot()
        events: List[SimEvent] = []
        violations: List[SimViolation] = []
        current_time: float = 0.0

        for reg_name in self._topo_order():
            reg = self.power_tree.nodes[reg_name]

            # ── PS-003: check parent is ON before enabling child ──────────────
            if reg.parent and reg.parent in self.power_tree.nodes:
                parent_state = self.states[reg.parent]
                if parent_state != RegulatorState.ON:
                    violations.append(SimViolation(
                        code="PS-003",
                        severity="error",
                        message=(
                            f"Regulator '{reg_name}' enabled before parent "
                            f"'{reg.parent}' is stable"
                        ),
                        time_ms=current_time,
                        scenario="boot",
                        component=reg_name,
                        detail=(
                            f"Parent '{reg.parent}' is in state "
                            f"'{self.states[reg.parent].value}' "
                            f"at t={current_time:.1f}ms"
                        ),
                        suggestion=(
                            f"Verify the regulator enable order: '{reg.parent}' "
                            f"must reach ON state before '{reg_name}' is asserted. "
                            f"Check regulator-enable-ramp-delay values."
                        ),
                    ))

            # ── RAMPING_UP transition ─────────────────────────────────────────
            old_state = self.states[reg_name].value
            self.states[reg_name] = RegulatorState.RAMPING_UP
            events.append(SimEvent(
                time_ms=current_time,
                component=reg_name,
                component_type="regulator",
                old_state=old_state,
                new_state=RegulatorState.RAMPING_UP.value,
            ))

            ramp_ms = (reg.startup_delay_us + reg.ramp_delay_us) / 1000.0
            current_time += ramp_ms

            # ── ON transition ─────────────────────────────────────────────────
            self.states[reg_name] = RegulatorState.ON
            self.stable_at[reg_name] = current_time
            events.append(SimEvent(
                time_ms=current_time,
                component=reg_name,
                component_type="regulator",
                old_state=RegulatorState.RAMPING_UP.value,
                new_state=RegulatorState.ON.value,
            ))

            # ── PS-001: stability window check ────────────────────────────────
            req_ms = self.stability_requirements.get(reg_name, 0.0)
            if req_ms > 0.0 and ramp_ms < req_ms:
                # Find consumers that might probe too early
                consumers = [
                    dev for dev, supplies in device_supplies.items()
                    if reg_name in supplies
                ]
                if consumers:
                    violations.append(SimViolation(
                        code="PS-001",
                        severity="warning",
                        message=(
                            f"'{reg_name}' may not be stable when "
                            f"{consumers[0]} probes: "
                            f"ramp={ramp_ms:.1f}ms < required {req_ms:.1f}ms"
                        ),
                        time_ms=current_time,
                        scenario="boot",
                        component=reg_name,
                        detail=(
                            f"Regulator ramp+startup = {ramp_ms:.1f}ms; "
                            f"stability requirement = {req_ms:.1f}ms. "
                            f"Consumers: {', '.join(consumers)}"
                        ),
                        suggestion=(
                            f"Add 'regulator-enable-ramp-delay = "
                            f"<{int(req_ms * 1000)}>;' to '{reg_name}' "
                            f"regulator node, or add a startup-delay-ms "
                            f"property to consumer devices."
                        ),
                    ))

        return events, violations

    # ── Suspend simulation ────────────────────────────────────────────────────

    def simulate_suspend(
        self,
        device_supplies: Dict[str, List[str]],
        device_probe_order: List[str],
        device_suspend_ms: float = 2.0,
    ) -> Tuple[List[SimEvent], List[SimViolation]]:
        """Simulate system suspend sequence.

        Devices suspend in reverse probe order.  After all devices are
        suspended, regulators are disabled leaf → root.

        Violation PS-002:
            A regulator is powered off while one or more consumer devices
            have not yet completed their suspend callback.

        Args:
            device_supplies: mapping device_name → list of supply names.
            device_probe_order: ordered list of device names (boot probe order).
            device_suspend_ms: simulated time per device suspend() call.

        Returns:
            (events, violations)
        """
        self.reset_to_on()
        events: List[SimEvent] = []
        violations: List[SimViolation] = []
        current_time: float = 0.0

        device_states: Dict[str, DeviceState] = {
            d: DeviceState.ACTIVE for d in device_probe_order
        }

        # ── Step 1: suspend devices in reverse probe order ────────────────────
        for dev in reversed(device_probe_order):
            device_states[dev] = DeviceState.SUSPENDING
            current_time += device_suspend_ms
            device_states[dev] = DeviceState.SUSPENDED
            events.append(SimEvent(
                time_ms=current_time,
                component=dev,
                component_type="device",
                old_state=DeviceState.ACTIVE.value,
                new_state=DeviceState.SUSPENDED.value,
            ))

        # ── Step 2: disable regulators leaf → root ────────────────────────────
        for reg_name in self._rev_topo_order():
            # Determine which devices powered by this rail are still active
            active_consumers = [
                dev for dev, supplies in device_supplies.items()
                if reg_name in supplies
                and device_states.get(dev, DeviceState.OFFLINE) != DeviceState.SUSPENDED
            ]

            if active_consumers:
                violations.append(SimViolation(
                    code="PS-002",
                    severity="error",
                    message=(
                        f"'{reg_name}' disabled at t={current_time:.1f}ms "
                        f"but {len(active_consumers)} consumer(s) not yet suspended"
                    ),
                    time_ms=current_time,
                    scenario="suspend",
                    component=reg_name,
                    detail=f"Active consumers: {', '.join(active_consumers)}",
                    suggestion=(
                        f"Ensure the following devices complete their suspend "
                        f"callback before '{reg_name}' is powered down: "
                        f"{', '.join(active_consumers)}. "
                        f"Check the regulator-off-in-suspend property ordering."
                    ),
                ))

            old_state = self.states[reg_name].value
            self.states[reg_name] = RegulatorState.SUSPENDED
            events.append(SimEvent(
                time_ms=current_time,
                component=reg_name,
                component_type="regulator",
                old_state=old_state,
                new_state=RegulatorState.SUSPENDED.value,
                triggered_by="suspend",
            ))
            current_time += 0.5

        return events, violations

    # ── Resume simulation ─────────────────────────────────────────────────────

    def simulate_resume(
        self,
        device_supplies: Dict[str, List[str]],
    ) -> Tuple[List[SimEvent], List[SimViolation]]:
        """Simulate resume from suspend.

        Calls simulate_boot() from the SUSPENDED state.  PS-003 catches any
        regulators re-enabled in wrong dependency order.
        """
        # Treat resume as re-running boot from suspended state
        # (suspended regulators behave the same as OFF for ordering purposes)
        for name in self.states:
            self.states[name] = RegulatorState.OFF
        self.stable_at.clear()

        events, violations = self.simulate_boot(device_supplies)

        # Re-tag scenario name
        for v in violations:
            v.scenario = "resume"
        for e in events:
            pass  # keep as-is

        return events, violations
