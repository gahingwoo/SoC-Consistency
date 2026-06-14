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
from .iommu_rules import register_iommu_rules
from .bus_rules import register_common_bus_rules
from .interrupt_rules import register_common_interrupt_rules
from .memory_rules import register_common_memory_rules


class GEN401OrphanedNode(BaseRule):
    """GEN-401: Orphaned Node"""

    code = "GEN-401"
    name = "Orphaned Node"
    description = "Detect nodes that are defined but never referenced."
    severity = "info"

    # Well-known structural / topology node names that are not peripheral
    # devices and should never be flagged as orphaned.
    _STRUCTURAL_EXACT: frozenset = frozenset({
        "aliases", "chosen", "cpus", "cpu-map", "memory", "reserved-memory",
        "firmware", "psci", "timer", "idle-states", "cpu-sleep",
        "thermal-zones", "clocks", "regulators", "pmu", "ports",
        "port", "endpoint", "trips", "cooling-maps",
        # SCMI / display / audio containers
        "scmi", "display-subsystem",
        # Misc infrastructure nodes that don't need supply/clock connections
        "iommu", "qos", "dfi", "msi-controller", "ppi-partitions",
    })
    _STRUCTURAL_PREFIX: tuple = (
        "l2-cache", "l3-cache", "cluster", "core", "opp-table", "opp-",
        "pmu-", "map",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check for orphaned device nodes.
        """
        violations: List[Violation] = []

        # simple check: devices with no supplies, clocks, or power-tree entry
        for device_name in list(model.devices.keys()):
            device = model.devices[device_name]

            # Nodes that have a numeric ``phandle`` property are referenced by
            # other nodes (e.g. clock controllers, reset controllers, syscon).
            # Flagging them as orphaned would be a false positive.
            if device.properties.get("phandle") is not None:
                continue

            # Only flag nodes that look like real peripheral device drivers.
            # Nodes without a ``compatible`` property are structural containers,
            # sub-nodes (pin groups, OPP entries, etc.) or pure config groups —
            # they are never standalone devices and don't need supply/clock refs.
            if device.properties.get("compatible") is None:
                continue

            # Skip well-known structural / CPU-topology nodes.
            base = device_name.split("@")[0]
            if base in self._STRUCTURAL_EXACT or any(
                base.startswith(p) for p in self._STRUCTURAL_PREFIX
            ):
                continue

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
    register_iommu_rules(registry, soc_name)
    register_netlist_rules(registry, soc_name)
    register_common_power_rules(registry, soc_name)
    register_thermal_rules(registry, soc_name)
    register_pin_rules(registry, soc_name)
    register_compat_rules(registry, soc_name)
    register_power_audit_rules(registry, soc_name)
    register_sec_rules(registry, soc_name)
    register_bw_rules(registry, soc_name)
    register_reg_rules(registry, soc_name)
    register_common_bus_rules(registry, soc_name)
    register_common_interrupt_rules(registry, soc_name)
    register_common_memory_rules(registry, soc_name)


__all__ = [
    "register_common_rules",
    "GEN401OrphanedNode",
    "register_netlist_rules",
    "register_common_power_rules",
    "register_pin_rules",
    "register_compat_rules",
    "register_power_audit_rules",
    "register_common_bus_rules",
    "register_common_interrupt_rules",
    "register_common_memory_rules",
]
