"""Rockchip clock rules."""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


class CK101ClockTreeCycleDetected(BaseRule):
    """CK-101: Clock Tree Cycle Detected"""

    code = "CK-101"
    name = "Clock Tree Cycle Detected"
    description = "The clock tree must be a tree or DAG with no cycles."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Check for cycles in the clock tree."""
        violations: List[Violation] = []

        cycles = model.clock_tree.detect_cycles()
        for cycle in cycles:
            violations.append(
                self._create_violation(
                    message=f"Clock cycle detected: {' -> '.join(cycle)}.",
                    impact="Clock tree initialization fails; system cannot boot.",
                    suggestion="Remove the cyclic parent clock reference; use the oscillator (OSC) as the root.",
                    location="/clocks",
                    affected_nodes=cycle[:-1],
                )
            )

        return violations


class CK102ClockProviderNotFound(BaseRule):
    """CK-102: Clock Provider Not Found"""

    code = "CK-102"
    name = "Clock Provider Not Found"
    description = "Clocks referenced by devices must have a registered provider."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Check that every device clock has a registered provider."""
        violations: List[Violation] = []

        for device_name, clock_list in model.device_clocks.items():
            for clock_name in clock_list:
                provider = model.clock_tree.find_provider(clock_name)
                if provider is None:
                    violations.append(
                        self._create_violation(
                            message=f"Device {device_name} requires clock {clock_name!r} but no provider is registered.",
                            impact=f"Device {device_name} driver cannot obtain its clock; device init fails.",
                            suggestion="Verify that the CRU is correctly defined and the referenced clock exists.",
                            location=f"/{device_name}",
                            affected_nodes=[device_name, clock_name],
                        )
                    )

        return violations


class CK104ClockProviderOrphaned(BaseRule):
    """CK-104: Clock Provider Orphaned"""

    code = "CK-104"
    name = "Clock Provider Orphaned"
    description = "Detect clock providers not used by any device."
    severity = "info"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Check for unused orphaned clock providers."""
        violations: List[Violation] = []

        orphaned = model.clock_tree.get_all_orphaned_providers()
        for provider_name in orphaned:
            violations.append(
                self._create_violation(
                    message=f"Clock provider {provider_name!r} is not used by any device.",
                    impact="Dead code; unnecessary power consumption and maintenance overhead.",
                    suggestion=f"Remove the unused clock definition or document its purpose.",
                    location=f"/clocks/{provider_name}",
                    affected_nodes=[provider_name],
                )
            )

        return violations


class CK103FrequencyOutOfRange(BaseRule):
    """CK-103: Clock Frequency Out of Specification"""

    code = "CK-103"
    name = "Frequency Out of Range"
    description = "Clock frequency must be within SoC specification."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check clock frequencies against the constraint specification.

        Constraint format:
        {
            "clocks": {
                "clk_cpu": {
                    "min_freq": 24e6,
                    "max_freq": 2.4e9
                }
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "clocks" in constraints:
            clocks_spec = constraints["clocks"]
            
            for clock_name, clock in model.clock_tree.clocks.items():
                if clock_name in clocks_spec:
                    spec = clocks_spec[clock_name]
                    spec_min = spec.get("min_freq", 0)
                    spec_max = spec.get("max_freq", float("inf"))
                    
                    if clock.rate > 0:  # only check clocks with a defined rate
                        if clock.rate < spec_min:
                            violations.append(
                                self._create_violation(
                                    message=f"Clock {clock_name!r} frequency {clock.rate/1e6:.0f} MHz "
                                        f"is below specification {spec_min/1e6:.0f} MHz.",
                                    impact="Device may malfunction; bus timeouts and I/O failures likely.",
                                    suggestion="Reduce the divider or increase the source frequency.",
                                    location=f"/clocks/{clock_name}",
                                    affected_nodes=[clock_name],
                                )
                            )
                        
                        if clock.rate > spec_max:
                            violations.append(
                                self._create_violation(
                                    message=f"Clock {clock_name!r} frequency {clock.rate/1e9:.2f} GHz "
                                        f"exceeds specification {spec_max/1e9:.2f} GHz.",
                                    impact="Overclocking causes thermal damage and chip degradation.",
                                    suggestion="Increase the divider or switch to a lower-frequency source.",
                                    location=f"/clocks/{clock_name}",
                                    affected_nodes=[clock_name],
                                )
                            )
        
        return violations


class CK105DividerInvalid(BaseRule):
    """CK-105: Clock Divider Invalid"""

    code = "CK-105"
    name = "Divider Invalid"
    description = "Clock divider must be a valid value."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check clock divider validity.

        Constraint format:
        {
            "clock_dividers": {
                "cpu": [1, 2, 4, 8, 16],  # allowed divider values
                "gpu": [1, 2, 4]
            }
        }
        """
        violations: List[Violation] = []
        
        # Demonstration rule; real implementation would parse dividers from DTS.
        
        constraints = context.metadata.get("constraints", {})
        if "clock_dividers" in constraints:
            divider_spec = constraints["clock_dividers"]
            
            # iterate clocks and check divider
            for clock_name, clock in model.clock_tree.clocks.items():
                if clock_name in divider_spec:
                    valid_dividers = divider_spec[clock_name]
                    
                    # validate if divider info is available
                    if hasattr(clock, "divider") and clock.divider not in valid_dividers:
                        violations.append(
                            self._create_violation(
                                message=f"Clock {clock_name!r} divider {clock.divider} "
                                        f"is not in the allowed set {valid_dividers}.",
                                impact="Wrong output frequency; device operates at incorrect rate.",
                                suggestion=f"Set divider to one of: {valid_dividers}.",
                                location=f"/clocks/{clock_name}",
                                affected_nodes=[clock_name],
                            )
                        )
        
        return violations


class CK106ClockSourceContention(BaseRule):
    """CK-106: Multiple Clock Sources Contention"""

    code = "CK-106"
    name = "Clock Source Contention"
    description = "Detect multiple devices contending on the same clock source."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Check for multiple devices contending on a single clock source."""
        violations: List[Violation] = []

        # Build a set of node names that are fixed-clock sources.
        # fixed-clocks (xin24m, xin32k, etc.) are reference oscillators and
        # are legitimately shared by all peripherals — not a contention issue.
        fixed_clock_labels: set = set()
        for dev_name, dev_node in model.devices.items():
            compat = dev_node.properties.get("compatible", "")
            if isinstance(compat, (list, tuple)):
                compat = " ".join(str(c) for c in compat)
            if "fixed-clock" in str(compat).lower():
                fixed_clock_labels.add(dev_name)
                fixed_clock_labels.add(f"&{dev_name}")

        # count device usage per clock
        clock_usage: dict = {}
        for device_name, clock_list in model.device_clocks.items():
            for clock_name in clock_list:
                if clock_name not in clock_usage:
                    clock_usage[clock_name] = []
                clock_usage[clock_name].append(device_name)

        # find clocks used by 3+ devices — but skip fixed-clock sources
        for clock_name, devices in clock_usage.items():
            # strip leading '&' for label-based lookup
            label = clock_name.lstrip("&")
            if clock_name in fixed_clock_labels or label in fixed_clock_labels:
                continue  # reference oscillator, sharing is expected
            # Pure-integer clock names are either clock-cell indices or
            # pre-resolved phandle numbers.  When shared by 50+ devices the
            # value is almost certainly the phandle of xin24m or a similar
            # system-wide reference clock — not true contention.
            if clock_name.lstrip("-").isdigit() and len(devices) >= 50:
                continue
            if len(devices) >= 3:
                violations.append(
                    self._create_violation(
                        message=f"Clock {clock_name!r} is shared by {len(devices)} devices: {', '.join(devices)}.",
                        impact="Clock contention degrades signal quality and increases jitter.",
                        suggestion="Assign dedicated clocks per device or reorganize the clock hierarchy.",
                        location=f"/clocks/{clock_name}",
                        affected_nodes=[clock_name] + devices,
                    )
                )

        return violations


def register_rockchip_clock_rules(registry, soc_name: str) -> None:
    """Register Rockchip clock rules."""
    registry.register(CK101ClockTreeCycleDetected(), soc_name)
    registry.register(CK102ClockProviderNotFound(), soc_name)
    registry.register(CK103FrequencyOutOfRange(), soc_name)
    registry.register(CK104ClockProviderOrphaned(), soc_name)
    registry.register(CK105DividerInvalid(), soc_name)
    registry.register(CK106ClockSourceContention(), soc_name)
