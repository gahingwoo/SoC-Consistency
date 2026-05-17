"""Allwinner-specific power domain rules."""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


# Allwinner uses a unified PMU (AXP series) connected over RSB or TWI.
# Every board must define a PMIC supply for CPU, GPU, and IO domains.
# The "aldo", "dldo", "dcdc" naming comes from AXP PMIC families.


class AW001PMICSupplyMissing(BaseRule):
    """AW-001: Required PMIC supply not defined in device tree."""

    code = "AW-001"
    name = "PMIC Supply Missing"
    description = (
        "Allwinner SoCs require a connected AXP PMIC; all regulator outputs "
        "used by the SoC must be declared in the device tree."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, supplies in model.device_supplies.items():
            for supply in supplies:
                if supply not in model.power_tree.nodes:
                    violations.append(
                        self._create_violation(
                            message=(
                                f"Device {device_name!r} references supply {supply!r} "
                                f"which is not declared (AXP PMIC output missing)."
                            ),
                            impact=(
                                f"Device {device_name!r} will not receive power; "
                                "driver probe will fail with regulator_get error."
                            ),
                            suggestion=(
                                f"Add regulator node {supply!r} under the AXP PMIC node "
                                "in your board DTS file."
                            ),
                            location=f"/{device_name}",
                            affected_nodes=[device_name, supply],
                        )
                    )

        return violations


class AW002RegulatorVoltageOutOfRange(BaseRule):
    """AW-002: Regulator voltage outside AXP PMIC valid range."""

    code = "AW-002"
    name = "Regulator Voltage Out of Range"
    description = (
        "AXP PMIC regulator output voltages must stay within the programmed "
        "range supported by the specific PMIC variant (AXP209/AXP803/AXP805/AXP313A)."
    )
    severity = "warning"

    # Typical valid CPU/SoC voltage range for Allwinner platforms
    _VALID_CPU_MIN = 0.81
    _VALID_CPU_MAX = 1.3

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for domain in model.power_tree.nodes.values():
            voltage = getattr(domain, "voltage", None)
            if voltage is None:
                continue

            node_name = domain.name.lower()
            if "cpu" in node_name or "dcdc" in node_name:
                if not (self._VALID_CPU_MIN <= voltage <= self._VALID_CPU_MAX):
                    violations.append(
                        self._create_violation(
                            message=(
                                f"Supply {domain.name!r} voltage {voltage:.2f}V is outside "
                                f"the expected CPU domain range "
                                f"[{self._VALID_CPU_MIN}V, {self._VALID_CPU_MAX}V]."
                            ),
                            impact="Incorrect voltage may damage the SoC or cause instability.",
                            suggestion=(
                                "Verify regulator-min-microvolt and regulator-max-microvolt "
                                "against the AXP PMIC datasheet for this board."
                            ),
                            location=f"/regulators/{domain.name}",
                            affected_nodes=[domain.name],
                        )
                    )

        return violations


class AW003PowerTreeCycle(BaseRule):
    """AW-003: Circular dependency in Allwinner power tree."""

    code = "AW-003"
    name = "Allwinner Power Tree Circular Dependency"
    description = (
        "The AXP PMIC supply chain must form a directed acyclic graph. "
        "Circular supply relationships will prevent the regulator framework from resolving boot order."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        cycles = model.power_tree.detect_cycles()
        for cycle in cycles:
            violations.append(
                self._create_violation(
                    message=f"Circular supply chain detected: {' -> '.join(cycle)}.",
                    impact="Kernel regulator framework will refuse to boot; system will hang in probe.",
                    suggestion="Remove the supply cycle by fixing the vin-supply phandles in the AXP PMIC subnode.",
                    location="/regulators",
                    affected_nodes=list(cycle),
                )
            )

        return violations
