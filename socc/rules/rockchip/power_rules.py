"""Rockchip power rules."""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


class PD001PowerDomainNotFound(BaseRule):
    """PD-001: Power Domain Not Found"""

    code = "PD-001"
    name = "Power Domain Not Found"
    description = "The pd-supply property of each device must reference a valid power domain."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check that every device power reference is valid.
        """
        violations: List[Violation] = []

        # iterate devices and their required supplies
        for device_name, supplies in model.device_supplies.items():
            for supply in supplies:
                # check supply exists
                if supply not in model.power_tree.nodes:
                    violations.append(
                        self._create_violation(
                            message=f"Device {device_name} requires power supply {supply!r} which is not defined.",
                            impact=f"Device {device_name} cannot obtain power; driver load will fail.",
                            suggestion=f"Define power node {supply!r} in the device tree or fix the phandle reference.",
                            location=f"/{device_name}",
                            affected_nodes=[device_name, supply],
                        )
                    )

        return violations


class PD003RegulatorCircularDependency(BaseRule):
    """PD-003: Regulator Circular Dependency"""

    code = "PD-003"
    name = "Regulator Circular Dependency"
    description = "Regulator supply relationships must form a DAG (no cycles)."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Detect circular dependencies in the power tree.
        """
        violations: List[Violation] = []

        cycles = model.power_tree.detect_cycles()
        for cycle in cycles:
            violations.append(
                self._create_violation(
                    message=f"Circular power dependency detected: {' -> '.join(cycle)}.",
                    impact="Power tree initialization fails; system hangs or loops during boot.",
                    suggestion="Inspect the supply chain and remove the cycle. Add an independent root power supply.",
                    location="/regulators",
                    affected_nodes=cycle[:-1],  # exclude duplicate tail node
                )
            )

        return violations


class PD006OrphanedRegulator(BaseRule):
    """PD-006: Orphaned Regulator"""

    code = "PD-006"
    name = "Orphaned Regulator"
    description = "Detect regulators not used by any device."
    severity = "info"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check for unused orphaned regulators.
        """
        violations: List[Violation] = []

        orphaned = model.power_tree.get_all_orphaned()
        for reg_name in orphaned:
            violations.append(
                self._create_violation(
                    message=f"Regulator {reg_name!r} is not used by any device.",
                    impact="Dead code increases maintenance burden and may cause unnecessary power draw.",
                    suggestion=f"Remove the unused regulator definition or document its intended use.",
                    location=f"/regulators/{reg_name}",
                    affected_nodes=[reg_name],
                )
            )

        return violations


class PD002RegulatorNotDefined(BaseRule):
    """PD-002: Regulator Not Defined in Constraints"""

    code = "PD-002"
    name = "Regulator Not Defined"
    description = "Regulators defined in the device tree must be declared in the SoC constraint file."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check that regulators are declared in the constraint file.
        """
        violations: List[Violation] = []
        
        # constraints are passed via context
        constraints = context.metadata.get("constraints", {})
        if "regulators" in constraints:
            defined_regulators = set(constraints["regulators"].keys())
            
            for reg_name in model.power_tree.nodes.keys():
                if reg_name not in defined_regulators:
                    violations.append(
                        self._create_violation(
                            message=f"Regulator {reg_name!r} is not declared in the SoC constraints.",
                            impact="Device tree disagrees with SoC spec; driver misconfiguration likely.",
                            suggestion=f"Add {reg_name!r} to the constraints file, or correct the regulator name in the DTS.",
                            location=f"/regulators/{reg_name}",
                            affected_nodes=[reg_name],
                        )
                    )
        
        return violations


class PD004VoltageOutOfRange(BaseRule):
    """PD-004: Voltage Range Out of Specification"""

    code = "PD-004"
    name = "Voltage Out of Range"
    description = "Regulator output voltage must be within SoC specification."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check regulator voltage ranges against the constraint spec.

        Constraint format:
        {
            "regulators": {
                "vdd_core": {
                    "min_voltage": 0.8,
                    "max_voltage": 1.1
                }
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "regulators" in constraints:
            regulators = constraints["regulators"]
            
            for reg_name, regulator in model.power_tree.nodes.items():
                if reg_name in regulators:
                    spec = regulators[reg_name]
                    spec_min = spec.get("min_voltage", 0)
                    spec_max = spec.get("max_voltage", float("inf"))
                    
                    # validate voltage bounds
                    if regulator.voltage_min < spec_min:
                        violations.append(
                            self._create_violation(
                                message=f"Regulator {reg_name!r} minimum voltage {regulator.voltage_min}V "
                                        f"is below specification {spec_min}V.",
                                impact="Under-voltage may cause logic failures.",
                                suggestion=f"Set minimum voltage to >= {spec_min}V.",
                                location=f"/regulators/{reg_name}",
                                affected_nodes=[reg_name],
                            )
                        )
                    
                    if regulator.voltage_max > spec_max:
                        violations.append(
                            self._create_violation(
                                message=f"Regulator {reg_name!r} maximum voltage {regulator.voltage_max}V "
                                        f"exceeds specification {spec_max}V.",
                                impact="Over-voltage may damage the chip and reduce lifespan.",
                                suggestion=f"Set maximum voltage to <= {spec_max}V.",
                                location=f"/regulators/{reg_name}",
                                affected_nodes=[reg_name],
                            )
                        )
        
        return violations


class PD005LoadImbalance(BaseRule):
    """PD-005: Regulator Load Imbalance"""

    code = "PD-005"
    name = "Load Imbalance"
    description = "Detect load imbalance on a single regulator."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check for load imbalance on a single regulator.
        """
        violations: List[Violation] = []
        
        # count loads per regulator
        regulator_loads = {reg: 0 for reg in model.power_tree.nodes.keys()}
        
        for device_name, supplies in model.device_supplies.items():
            for supply in supplies:
                if supply in regulator_loads:
                    regulator_loads[supply] += 1
        
        # compute average load
        total_loads = sum(regulator_loads.values())
        if total_loads > 0:
            avg_load = total_loads / len(regulator_loads)
            threshold = avg_load * 1.5  # allow up to 1.5x average
            
            for reg_name, load_count in regulator_loads.items():
                if load_count > threshold and load_count > 2:
                    violations.append(
                        self._create_violation(
                            message=f"Regulator {reg_name!r} load ({load_count} devices) "
                                    f"is >1.5x the average ({avg_load:.1f}).",
                            impact="A single failure affects many devices, reducing reliability.",
                            suggestion="Distribute load across multiple regulators or add a backup.",
                            location=f"/regulators/{reg_name}",
                            affected_nodes=[reg_name],
                        )
                    )
        
        return violations


def register_rockchip_power_rules(registry, soc_name: str) -> None:
    """Register Rockchip power rules."""
    registry.register(PD001PowerDomainNotFound(), soc_name)
    registry.register(PD002RegulatorNotDefined(), soc_name)
    registry.register(PD003RegulatorCircularDependency(), soc_name)
    registry.register(PD004VoltageOutOfRange(), soc_name)
    registry.register(PD005LoadImbalance(), soc_name)
    registry.register(PD006OrphanedRegulator(), soc_name)
