"""Common interrupt rules (IRQ, FIQ, etc.).

Constraint-metadata driven and SoC-agnostic; fires only when the caller
supplies ``interrupt_allocation`` / ``interrupt_priority`` keys.
"""

from typing import List, Dict, Set
from socc.model import SoC
from socc.rules.base import BaseRule, Violation, CheckContext


class IRQ501DuplicateInterrupt(BaseRule):
    """IRQ-501: Duplicate Interrupt"""

    code = "IRQ-501"
    name = "Duplicate Interrupt"
    description = "An interrupt number must not be assigned to more than one device."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check for duplicate IRQ number assignments.

        Constraint format:
        {
            "interrupt_allocation": {
                "uart0": {"irq": 32, "type": "level"},
                "uart1": {"irq": 33, "type": "level"},
                "spi0": {"irq": 40, "type": "edge"}
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "interrupt_allocation" not in constraints:
            return violations
        
        irq_alloc = constraints["interrupt_allocation"]
        
        # count devices per IRQ number
        irq_usage: Dict[int, List[str]] = {}
        
        for dev_name, config in irq_alloc.items():
            irq_num = config.get("irq", -1)
            if irq_num >= 0:
                if irq_num not in irq_usage:
                    irq_usage[irq_num] = []
                irq_usage[irq_num].append(dev_name)
        
        # find IRQs with multiple assignees
        for irq_num, devices in irq_usage.items():
            if len(devices) > 1:
                violations.append(
                    self._create_violation(
                        message=f"IRQ{irq_num} is assigned to multiple devices: {', '.join(devices)}.",
                        impact="Interrupt controller cannot distinguish sources; IRQs are lost or mishandled.",
                        suggestion=f"Assign a unique IRQ number to each device; do not reuse IRQ{irq_num}.",
                        location=f"/interrupt/{irq_num}",
                        affected_nodes=[f"IRQ{irq_num}"] + devices,
                    )
                )
        
        return violations


class IRQ502InterruptPriorityConflict(BaseRule):
    """IRQ-502: Interrupt Priority Conflict"""

    code = "IRQ-502"
    name = "Priority Conflict"
    description = "Interrupt priorities must be correctly assigned."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check interrupt priority assignments.

        Constraint format:
        {
            "interrupt_priority": {
                "timer": {"irq": 27, "priority": 0},      # highest priority
                "uart0": {"irq": 32, "priority": 4},
                "spi0": {"irq": 40, "priority": 8},
                "gpio": {"irq": 48, "priority": 15}       # lowest priority
            },
            "priority_constraints": {
                "min_priority": 0,
                "max_priority": 15,
                "critical_priority": 2
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        irq_priority = constraints.get("interrupt_priority", {})
        priority_spec = constraints.get("priority_constraints", {})
        
        if not irq_priority:
            return violations
        
        min_p = priority_spec.get("min_priority", 0)
        max_p = priority_spec.get("max_priority", 15)
        critical_p = priority_spec.get("critical_priority", 2)
        
        # map priority -> device names
        priority_dist: Dict[int, List[str]] = {}
        
        for dev_name, config in irq_priority.items():
            priority = config.get("priority", -1)
            
            # check priority bounds
            if priority < min_p or priority > max_p:
                violations.append(
                    self._create_violation(
                        message=f"Device {dev_name!r} interrupt priority {priority} is outside [{min_p}, {max_p}].",
                        impact="Invalid priority; interrupt controller may not handle it correctly.",
                        suggestion=f"Set priority to a value in [{min_p}, {max_p}].",
                        location=f"/interrupt/{dev_name}",
                        affected_nodes=[dev_name],
                    )
                )
            else:
                if priority not in priority_dist:
                    priority_dist[priority] = []
                priority_dist[priority].append(dev_name)
        
        # critical devices must get high priority
        critical_devices = ["timer", "watchdog"]
        
        for dev_name, config in irq_priority.items():
            priority = config.get("priority", max_p)
            is_critical = any(crit in dev_name.lower() for crit in critical_devices)
            
            if is_critical and priority > critical_p:
                violations.append(
                    self._create_violation(
                        message=f"Critical device {dev_name!r} has priority {priority}; "
                                f"it should be <= {critical_p} (lower = higher priority).",
                        impact="Delayed critical interrupt may cause system failure (e.g. watchdog timeout).",
                        suggestion=f"Raise {dev_name!r} priority to {critical_p} or lower.",
                        location=f"/interrupt/{dev_name}",
                        affected_nodes=[dev_name],
                    )
                )
        
        return violations


def register_common_interrupt_rules(registry, soc_name: str = "common") -> None:
    """Register common interrupt rules."""
    registry.register(IRQ501DuplicateInterrupt(), soc_name)
    registry.register(IRQ502InterruptPriorityConflict(), soc_name)
