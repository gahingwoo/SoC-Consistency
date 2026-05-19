"""Clock tree state machine simulation.

Simulates clock gate / ungate operations and detects clock-gating
violations (CG-001, CG-002).
"""

from __future__ import annotations

import fnmatch
from typing import Dict, List, Optional, Set, Tuple

from socc.model.clock import ClockTree
from socc.simulation.types import (
    ClockState, DeviceState, SimEvent, SimViolation
)


class ClockStateMachine:
    """Behavioural model of a SoC clock tree across boot / suspend / resume.

    All times are in milliseconds.
    """

    def __init__(
        self,
        clock_tree: ClockTree,
        gating_constraints: Optional[List[dict]] = None,
    ):
        """
        Args:
            clock_tree: The SoC clock tree model.
            gating_constraints: list of dicts from simulation_constraints →
                clock_gating in the SoC YAML.  Each entry has:
                  - clock_pattern (glob): which clocks the rule applies to
                  - consumers_must_idle_before_gate (bool)
        """
        self.clock_tree = clock_tree
        self.gating_constraints: List[dict] = gating_constraints or []

        self.states: Dict[str, ClockState] = {
            name: ClockState.DISABLED for name in clock_tree.clocks
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _topo_order(self) -> List[str]:
        """Return clocks in topological order (root first, leaves last)."""
        visited: Set[str] = set()
        order: List[str] = []

        def _dfs(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            clk = self.clock_tree.clocks.get(name)
            if clk and clk.parent and clk.parent in self.clock_tree.clocks:
                _dfs(clk.parent)
            order.append(name)

        for name in self.clock_tree.clocks:
            _dfs(name)
        return order

    def _children_of(self, clock_name: str) -> List[str]:
        """Return direct child clocks of *clock_name*."""
        return [
            name for name, clk in self.clock_tree.clocks.items()
            if clk.parent == clock_name
        ]

    def _must_idle_before_gate(self, clock_name: str) -> bool:
        """Return True if the gating constraint requires consumers to be idle."""
        for constraint in self.gating_constraints:
            pattern = constraint.get("clock_pattern", "")
            if fnmatch.fnmatch(clock_name, f"*{pattern}*"):
                return bool(constraint.get("consumers_must_idle_before_gate", True))
        return True  # conservative default

    def reset_to_disabled(self) -> None:
        for name in self.states:
            self.states[name] = ClockState.DISABLED

    def reset_to_enabled(self) -> None:
        for name in self.states:
            self.states[name] = ClockState.ENABLED

    # ── Boot / enable ─────────────────────────────────────────────────────────

    def simulate_enable(
        self,
        current_time: float = 0.0,
    ) -> Tuple[List[SimEvent], List[SimViolation]]:
        """Enable all clocks in topological order during boot.

        No violations are generated here; this populates state for later
        checks in suspend / resume.

        Returns:
            (events, [])  — events only, no violations during clean enable.
        """
        self.reset_to_disabled()
        events: List[SimEvent] = []

        for clk_name in self._topo_order():
            old = self.states[clk_name].value
            self.states[clk_name] = ClockState.ENABLING
            self.states[clk_name] = ClockState.ENABLED
            events.append(SimEvent(
                time_ms=current_time,
                component=clk_name,
                component_type="clock",
                old_state=old,
                new_state=ClockState.ENABLED.value,
            ))
            current_time += 0.01  # negligible gating latency

        return events, []

    # ── Power-domain gating (triggered by regulator going OFF) ────────────────

    def simulate_power_off_impact(
        self,
        disabled_regulator: str,
        device_supplies: Dict[str, List[str]],
        device_clocks: Dict[str, List[str]],
        device_states: Dict[str, DeviceState],
        current_time: float = 0.0,
    ) -> Tuple[List[SimEvent], List[SimViolation]]:
        """Detect violations when a power domain rail goes off.

        When *disabled_regulator* powers off, every device whose primary
        supply includes that regulator loses power.  Any clocks those devices
        consume that are still ENABLED should generate violations.

        CG-001: A clock is gated (power went off) while the consumer device
                is still reported as ACTIVE.

        CG-002: A parent clock is disabled while one of its child clocks still
                has active consumers.

        Returns:
            (events, violations)
        """
        events: List[SimEvent] = []
        violations: List[SimViolation] = []

        # Devices powered by this regulator
        powered_devices = {
            dev for dev, supplies in device_supplies.items()
            if disabled_regulator in supplies
        }

        # Collect clocks consumed by those devices
        affected_clocks: Set[str] = set()
        for dev in powered_devices:
            for clk in device_clocks.get(dev, []):
                affected_clocks.add(clk)

        for clk_name in affected_clocks:
            if self.states.get(clk_name) != ClockState.ENABLED:
                continue

            # Find devices consuming this clock that are still ACTIVE.
            # device_clocks is keyed by device name (dev → [clk1, clk2, ...]),
            # so we must iterate items() and check membership, not .get(clk_name).
            active_consumers = [
                dev for dev, clks in device_clocks.items()
                if clk_name in clks and device_states.get(dev) == DeviceState.ACTIVE
            ]

            # Also check all consumers listed in the Clock node itself
            # (covers models built without a device_clocks mapping).
            clk_obj = self.clock_tree.clocks.get(clk_name)
            if clk_obj:
                for consumer in clk_obj.consumers:
                    if (consumer not in active_consumers
                            and device_states.get(consumer) == DeviceState.ACTIVE):
                        active_consumers.append(consumer)

            if active_consumers and self._must_idle_before_gate(clk_name):
                violations.append(SimViolation(
                    code="CG-001",
                    severity="error",
                    message=(
                        f"Clock '{clk_name}' gated by power-off of "
                        f"'{disabled_regulator}' while "
                        f"{len(active_consumers)} device(s) still active"
                    ),
                    time_ms=current_time,
                    scenario="suspend",
                    component=clk_name,
                    detail=(
                        f"Active consumers: {', '.join(active_consumers)}. "
                        f"Power rail '{disabled_regulator}' was disabled."
                    ),
                    suggestion=(
                        f"Ensure devices {', '.join(active_consumers)} "
                        f"complete runtime PM suspend before '{disabled_regulator}' "
                        f"is powered off. Add the appropriate regulator-off-in-suspend "
                        f"or runtime_pm autosuspend delay."
                    ),
                ))

            # Gate the clock
            old_state = self.states[clk_name].value
            self.states[clk_name] = ClockState.DISABLED
            events.append(SimEvent(
                time_ms=current_time,
                component=clk_name,
                component_type="clock",
                old_state=old_state,
                new_state=ClockState.DISABLED.value,
                triggered_by=disabled_regulator,
            ))

        return events, violations

    # ── Parent-clock gating cascade ───────────────────────────────────────────

    def check_parent_clock_violations(
        self,
        device_clocks: Dict[str, List[str]],
        device_states: Dict[str, DeviceState],
        current_time: float = 0.0,
    ) -> List[SimViolation]:
        """Detect CG-002: parent clock disabled while child consumers active.

        This is run after simulate_power_off_impact to catch secondary effects
        where a now-DISABLED parent clock leaves orphaned child clocks.

        CG-002 applies when:
          - A clock transitions to DISABLED
          - It has child clocks that are still ENABLED
          - Those child clocks have active consumers

        Returns:
            List of CG-002 violations.
        """
        violations: List[SimViolation] = []

        for clk_name, state in self.states.items():
            if state != ClockState.DISABLED:
                continue

            # Check each child clock
            for child_name in self._children_of(clk_name):
                if self.states.get(child_name) != ClockState.ENABLED:
                    continue

                # Find active consumers of the child clock
                child_clk = self.clock_tree.clocks.get(child_name)
                active_consumers: List[str] = []
                for dev, clks in device_clocks.items():
                    if child_name in clks and device_states.get(dev) == DeviceState.ACTIVE:
                        active_consumers.append(dev)
                if child_clk:
                    for consumer in child_clk.consumers:
                        if (consumer not in active_consumers
                                and device_states.get(consumer) == DeviceState.ACTIVE):
                            active_consumers.append(consumer)

                if active_consumers:
                    violations.append(SimViolation(
                        code="CG-002",
                        severity="error",
                        message=(
                            f"Parent clock '{clk_name}' disabled while "
                            f"child '{child_name}' still has "
                            f"{len(active_consumers)} active consumer(s)"
                        ),
                        time_ms=current_time,
                        scenario="suspend",
                        component=clk_name,
                        detail=(
                            f"Child '{child_name}' consumers still active: "
                            f"{', '.join(active_consumers)}"
                        ),
                        suggestion=(
                            f"Disable '{child_name}' (and its consumers) "
                            f"before gating parent '{clk_name}'. "
                            f"Check clock disable sequence in driver probe/remove."
                        ),
                    ))

        return violations
