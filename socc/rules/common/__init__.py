"""Common rules applicable to all SoC targets."""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext
from .netlist_rules import register_netlist_rules
from .power_rules import register_common_power_rules
from .thermal_rules import register_thermal_rules
from .pin_rules import register_pin_rules
from .compat_rules import register_compat_rules
from .power_audit_rules import register_power_audit_rules
from .sec_rules import register_sec_rules
from .bw_rules import register_bw_rules
from .reg_rules import register_reg_rules


class GEN401OrphanedNode(BaseRule):
    """GEN-401: Orphaned Node"""

    code = "GEN-401"
    name = "Orphaned Node"
    description = "Detect nodes that are defined but never referenced."
    severity = "info"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check for orphaned device nodes.
        """
        violations: List[Violation] = []

        # simple check: devices with no supplies, clocks, or power-tree entry
        for device_name in list(model.devices.keys()):
            has_supplies = device_name in model.device_supplies
            has_clocks = device_name in model.device_clocks
            # check power-tree membership and clock consumers
            in_power_tree = device_name in model.power_tree.nodes
            in_clock_consumers = any(
                device_name in clock.consumers 
                for clock in model.clock_tree.clocks.values()
            )

            if not (has_supplies or has_clocks or in_power_tree or in_clock_consumers):
                violations.append(
                    self._create_violation(
                        message=f"Device {device_name!r} is not referenced by any other node.",
                        impact="Dead code; increases maintenance burden.",
                        suggestion="Remove the orphaned node or document its purpose.",
                        location=f"/{device_name}",
                        affected_nodes=[device_name],
                    )
                )

        return violations


def register_common_rules(registry, soc_name: str = "common") -> None:
    """Register common rules into *registry*."""
    registry.register(GEN401OrphanedNode(), soc_name)
    register_netlist_rules(registry, soc_name)
    register_common_power_rules(registry, soc_name)
    register_thermal_rules(registry, soc_name)
    register_pin_rules(registry, soc_name)
    register_compat_rules(registry, soc_name)
    register_power_audit_rules(registry, soc_name)
    register_sec_rules(registry, soc_name)
    register_bw_rules(registry, soc_name)
    register_reg_rules(registry, soc_name)


__all__ = [
    "register_common_rules",
    "GEN401OrphanedNode",
    "register_netlist_rules",
    "register_common_power_rules",
    "register_pin_rules",
    "register_compat_rules",
    "register_power_audit_rules",
]
