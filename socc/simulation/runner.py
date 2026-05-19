"""Scenario runner — orchestrates all three state machines.

Usage::

    from socc.simulation.runner import ScenarioRunner
    from socc.simulation._constraints import load_sim_constraints, stability_requirements_from_constraints

    constraints = load_sim_constraints("data/soc/rockchip/rk3588.yaml")
    runner = ScenarioRunner(soc_model, constraints)

    result = runner.run("boot")          # → ScenarioResult
    all_results = runner.run_all()       # → Dict[str, ScenarioResult]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from socc.model.soc import SoC
from socc.simulation._constraints import stability_requirements_from_constraints
from socc.simulation.clock_sim import ClockStateMachine
from socc.simulation.power_sim import PowerStateMachine
from socc.simulation.reset_sim import ResetStateMachine
from socc.simulation.types import DeviceState, RegulatorState, ScenarioResult, SimViolation


# Scenarios supported by this runner
SCENARIOS = ["boot", "suspend", "resume", "runtime_pm"]
SCENARIO_DESCRIPTIONS = {
    "boot":       "Simulate full power-on and device probe sequence",
    "suspend":    "Simulate Linux PM suspend (s2idle / deep)",
    "resume":     "Simulate resume from suspend back to full operation",
    "runtime_pm": "Simulate runtime PM autosuspend / wake cycles",
}


class ScenarioRunner:
    """Orchestrates PowerStateMachine, ClockStateMachine, and ResetStateMachine
    through a named scenario and returns a ScenarioResult.
    """

    def __init__(self, model: SoC, constraints: Optional[Dict[str, Any]] = None):
        """
        Args:
            model: Populated SoC model (from dts_mapper or build_sample_model).
            constraints: Parsed simulation_constraints block.  If None, all
                         state machines use zero-delay / permissive defaults.
        """
        self.model = model
        self.constraints: Dict[str, Any] = constraints or {}

        stability = stability_requirements_from_constraints(self.constraints)
        gating    = self.constraints.get("clock_gating", [])

        self.power_sm = PowerStateMachine(
            model.power_tree, stability_requirements=stability
        )
        self.clock_sm = ClockStateMachine(
            model.clock_tree, gating_constraints=gating
        )
        self.reset_sm = ResetStateMachine(
            reset_dependencies=self.constraints.get("reset_dependencies", []),
            required_resets_patterns=self.constraints.get("required_resets_patterns", []),
        )

    # ── Device helpers ────────────────────────────────────────────────────────

    def _device_probe_order(self) -> List[str]:
        """Return a stable deterministic probe order (sorted by DTS path)."""
        return sorted(self.model.devices.keys(),
                      key=lambda n: self.model.devices[n].path or n)

    # ── Scenario dispatch ─────────────────────────────────────────────────────

    def run(self, scenario: str) -> ScenarioResult:
        """Run a single named scenario and return its result.

        Args:
            scenario: One of 'boot', 'suspend', 'resume', 'runtime_pm'.

        Returns:
            ScenarioResult with timeline and violations.

        Raises:
            ValueError: if *scenario* is not recognised.
        """
        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario '{scenario}'. "
                f"Valid choices: {', '.join(SCENARIOS)}"
            )

        method = {
            "boot":       self._run_boot,
            "suspend":    self._run_suspend,
            "resume":     self._run_resume,
            "runtime_pm": self._run_runtime_pm,
        }[scenario]

        return method()

    def run_all(self) -> Dict[str, ScenarioResult]:
        """Run all four scenarios and return a mapping of name → result."""
        return {s: self.run(s) for s in SCENARIOS}

    # ── Individual scenarios ──────────────────────────────────────────────────

    def _run_boot(self) -> ScenarioResult:
        result = ScenarioResult(scenario="boot")

        # ── Power simulation ──────────────────────────────────────────────────
        power_events, power_violations = self.power_sm.simulate_boot(
            self.model.device_supplies
        )
        result.timeline.extend(power_events)
        result.violations.extend(power_violations)
        last_t = power_events[-1].time_ms if power_events else 0.0

        # ── Clock enable ──────────────────────────────────────────────────────
        clock_events, _ = self.clock_sm.simulate_enable(current_time=last_t)
        result.timeline.extend(clock_events)
        last_t = clock_events[-1].time_ms if clock_events else last_t

        # ── Reset simulation ──────────────────────────────────────────────────
        # By the time boot completes the CRU is assumed ready
        reset_events, reset_violations = self.reset_sm.simulate_boot_deassert(
            self.model.devices,
            provider_ready=["cru"],
            current_time=last_t,
        )
        result.timeline.extend(reset_events)
        result.violations.extend(reset_violations)

        # RS-002: missing resets properties
        missing_violations = self.reset_sm.check_missing_resets(self.model.devices)
        result.violations.extend(missing_violations)

        if result.timeline:
            result.duration_ms = result.timeline[-1].time_ms
        return result

    def _run_suspend(self) -> ScenarioResult:
        result = ScenarioResult(scenario="suspend")
        probe_order = self._device_probe_order()

        # ── Start with clocks enabled (as they would be at runtime) ──────────
        self.clock_sm.reset_to_enabled()

        # ── Power simulation ──────────────────────────────────────────────────
        power_events, power_violations = self.power_sm.simulate_suspend(
            self.model.device_supplies,
            device_probe_order=probe_order,
        )
        result.timeline.extend(power_events)
        result.violations.extend(power_violations)

        # ── Clock gating impact ───────────────────────────────────────────────
        # Build a snapshot of device states (all SUSPENDED after power sim)
        device_states: Dict[str, DeviceState] = {
            dev: DeviceState.SUSPENDED for dev in probe_order
        }

        # For each regulator that went off, check clock gating
        current_t = power_events[-1].time_ms if power_events else 0.0
        for reg_name in self.model.power_tree.nodes:
            if self.power_sm.states.get(reg_name) == RegulatorState.SUSPENDED:
                clk_events, clk_violations = self.clock_sm.simulate_power_off_impact(
                    disabled_regulator=reg_name,
                    device_supplies=self.model.device_supplies,
                    device_clocks=self.model.device_clocks,
                    device_states=device_states,
                    current_time=current_t,
                )
                result.timeline.extend(clk_events)
                result.violations.extend(clk_violations)

        # CG-002: parent clock disabled while children have active consumers
        cg002 = self.clock_sm.check_parent_clock_violations(
            self.model.device_clocks,
            device_states,
            current_time=current_t,
        )
        result.violations.extend(cg002)

        if result.timeline:
            result.duration_ms = result.timeline[-1].time_ms
        return result

    def _run_resume(self) -> ScenarioResult:
        result = ScenarioResult(scenario="resume")

        # Resume re-runs the boot power sequence from suspended state
        power_events, power_violations = self.power_sm.simulate_resume(
            self.model.device_supplies
        )
        result.timeline.extend(power_events)
        result.violations.extend(power_violations)
        last_t = power_events[-1].time_ms if power_events else 0.0

        # Clock re-enable
        clock_events, _ = self.clock_sm.simulate_enable(current_time=last_t)
        result.timeline.extend(clock_events)

        if result.timeline:
            result.duration_ms = result.timeline[-1].time_ms
        return result

    def _run_runtime_pm(self) -> ScenarioResult:
        """Simulate a single runtime PM autosuspend + wake cycle.

        Runs one suspend followed by one resume and collects violations from
        both.  This catches scenarios where a device's runtime-pm callback
        doesn't properly sequence clocks/power before suspending.
        """
        result = ScenarioResult(scenario="runtime_pm")

        suspend_result = self._run_suspend()
        resume_result  = self._run_resume()

        result.timeline  = suspend_result.timeline + resume_result.timeline
        result.violations = suspend_result.violations + resume_result.violations
        for v in result.violations:
            v.scenario = "runtime_pm"

        if result.timeline:
            result.duration_ms = result.timeline[-1].time_ms
        return result
