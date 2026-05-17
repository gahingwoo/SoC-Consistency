"""Amlogic Meson power domain rules.

Amlogic SoCs have two power planes:
- EE (External Entity): the main SoC logic domain, powered off in deep sleep
- AO (Always-On): survives suspend; hosts the UART debug console, IR receiver, etc.

Mixing AO and EE supplies is a common DTS authoring mistake that leads to
boot failures or broken suspend/resume behavior.
"""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


class ML001AOEEDomainMismatch(BaseRule):
    """ML-001: Device in AO domain references EE supply (or vice versa)."""

    code = "ML-001"
    name = "AO/EE Domain Mismatch"
    description = (
        "Amlogic SoCs separate the Always-On (AO) and External-Entity (EE) "
        "power planes.  Devices in the AO domain must use AO supplies; "
        "EE devices must use EE supplies.  Mismatches cause resume failures."
    )
    severity = "error"

    # Heuristic: AO node names contain 'ao' prefix; EE supplies are vdd_ee / vcc_*
    _AO_PREFIXES = ("ao_", "uart_ao", "i2c_ao", "ir_")
    _EE_SUPPLIES = ("vdd_ee", "vcc_3v3", "vcc_1v8", "vdd_cpu", "vdd_gpu")

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, supplies in model.device_supplies.items():
            is_ao_device = any(device_name.lower().startswith(p) for p in self._AO_PREFIXES)
            if not is_ao_device:
                continue

            for supply in supplies:
                if any(supply.lower().startswith(ee) for ee in self._EE_SUPPLIES):
                    violations.append(
                        self._create_violation(
                            message=(
                                f"AO-domain device {device_name!r} references EE-domain "
                                f"supply {supply!r}. AO devices must use vddao or ao_* supplies."
                            ),
                            impact=(
                                "During suspend the EE power plane is cut; "
                                f"{device_name!r} will lose power unexpectedly, "
                                "causing kernel panic or corrupted device state on resume."
                            ),
                            suggestion=(
                                f"Change the supply phandle for {device_name!r} to vddao "
                                "or an AO-plane regulator output."
                            ),
                            location=f"/{device_name}",
                            affected_nodes=[device_name, supply],
                        )
                    )

        return violations


class ML002MissingVDDAO(BaseRule):
    """ML-002: vddao supply not defined in device tree."""

    code = "ML-002"
    name = "vddao Supply Missing"
    description = (
        "The Amlogic AO power domain requires a dedicated vddao supply. "
        "Without it, the always-on domain (debug UART, IR, RTC) cannot function."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        if not model.power_tree.nodes:
            return violations

        has_vddao = any(
            "vddao" in node_name.lower() or "ao" in node_name.lower()
            for node_name in model.power_tree.nodes
        )

        if not has_vddao:
            violations.append(
                self._create_violation(
                    message="No vddao (Always-On) supply found in the power tree.",
                    impact=(
                        "Amlogic AO domain devices (debug UART, IR receiver, RTC) "
                        "will be unable to probe; system may fail to boot."
                    ),
                    suggestion=(
                        "Add a regulator node for the vddao supply in your board DTS. "
                        "Typically this is a fixed 0.9V-1.0V output from the PMIC."
                    ),
                    location="/regulators",
                    affected_nodes=["vddao"],
                )
            )

        return violations


class ML003PowerTreeCycle(BaseRule):
    """ML-003: Circular dependency in Amlogic power tree."""

    code = "ML-003"
    name = "Amlogic Power Tree Circular Dependency"
    description = (
        "The regulator supply chain on Amlogic boards must form a directed acyclic graph. "
        "Circular vin-supply references will prevent the regulator framework from probing."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        cycles = model.power_tree.detect_cycles()
        for cycle in cycles:
            violations.append(
                self._create_violation(
                    message=f"Circular supply dependency detected: {' -> '.join(cycle)}.",
                    impact="Kernel regulator subsystem will hang during probe sequence.",
                    suggestion=(
                        "Fix the vin-supply phandles so that supply relationships "
                        "form a tree rooted at vcc_5v or the board input supply."
                    ),
                    location="/regulators",
                    affected_nodes=list(cycle),
                )
            )

        return violations
