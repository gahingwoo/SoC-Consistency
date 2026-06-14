"""Rockchip bus rules (AHB, APB, AXI, etc.)."""

from typing import List, Dict, Set
from socc.model import SoC
from socc.rules.base import BaseRule, Violation, CheckContext


class BUS401SlaveAddressCollision(BaseRule):
    """BUS-401: Slave Address Collision"""

    code = "BUS-401"
    name = "Slave Address Collision"
    description = "Bus slave address ranges must not overlap."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check for overlapping bus slave address ranges.

        Constraint format:
        {
            "bus_slaves": {
                "uart0": {"base": 0xFF110000, "size": 0x100},
                "uart1": {"base": 0xFF120000, "size": 0x100},
                "spi0": {"base": 0xFF130000, "size": 0x1000}
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "bus_slaves" not in constraints:
            return violations
        
        bus_slaves = constraints["bus_slaves"]
        
        # collect slave address ranges
        slaves = []
        for dev_name, config in bus_slaves.items():
            base = config.get("base", 0)
            size = config.get("size", 0)
            slaves.append((base, base + size, dev_name))
        
        slaves.sort()
        
        # check for overlaps
        for i in range(len(slaves)):
            for j in range(i + 1, len(slaves)):
                base_i, end_i, name_i = slaves[i]
                base_j, end_j, name_j = slaves[j]
                
                # check for address overlap
                if base_i < end_j and base_j < end_i:
                    violations.append(
                        self._create_violation(
                            message=f"Slave {name_i!r} (0x{base_i:X}-0x{end_i:X}) overlaps "
                                    f"slave {name_j!r} (0x{base_j:X}-0x{end_j:X}).",
                            impact="Address decode conflict; multiple slaves respond to the same address, causing bus contention.",
                            suggestion="Reassign slave base addresses to eliminate the overlap.",
                            location="/bus/slaves",
                            affected_nodes=[name_i, name_j],
                        )
                    )
        
        return violations


class BUS402FrequencyMismatch(BaseRule):
    """BUS-402: Bus Frequency Mismatch"""

    code = "BUS-402"
    name = "Frequency Mismatch"
    description = "Bus frequency must be compatible with all connected devices."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check bus frequency compatibility with slave devices.

        Constraint format:
        {
            "bus_frequency": {
                "ahb": 200e6,      # AHB bus frequency
                "apb": 100e6       # APB bus frequency
            },
            "device_bus_freq": {
                "uart0": 100e6,
                "spi0": 200e6,
                "i2c0": 100e6
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        bus_freq = constraints.get("bus_frequency", {})
        dev_freq = constraints.get("device_bus_freq", {})
        
        if not (bus_freq and dev_freq):
            return violations
        
        # simplified check: device freq must not exceed bus freq
        for dev_name, dev_f in dev_freq.items():
            # infer bus from device name
            if "uart" in dev_name or "i2c" in dev_name:
                bus_type = "apb"
            elif "spi" in dev_name or "emmc" in dev_name:
                bus_type = "ahb"
            else:
                continue
            
            if bus_type in bus_freq:
                bus_f = bus_freq[bus_type]
                
                if dev_f > bus_f:
                    violations.append(
                        self._create_violation(
                            message=f"Device {dev_name!r} at {dev_f/1e6:.0f} MHz "
                                    f"exceeds {bus_type.upper()} bus frequency {bus_f/1e6:.0f} MHz.",
                            impact="Device runs faster than the bus; timing errors and data loss likely.",
                            suggestion=f"Lower device frequency to <= {bus_f/1e6:.0f} MHz or raise the bus frequency.",
                            location=f"/bus/{bus_type}/{dev_name}",
                            affected_nodes=[dev_name, bus_type],
                        )
                    )
        
        return violations


class BUS403SlaveResponseTimeout(BaseRule):
    """BUS-403: Slave Response Timeout"""

    code = "BUS-403"
    name = "Slave Response Timeout"
    description = "Slave response latency must be within specification."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check slave response latency.

        Constraint format:
        {
            "slave_latency": {
                "uart0": {"max_latency": 100},    # nanoseconds
                "spi0": {"max_latency": 50},
                "ddr": {"max_latency": 200}
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "slave_latency" not in constraints:
            return violations
        
        latency_spec = constraints["slave_latency"]
        
        for slave_name, spec in latency_spec.items():
            max_latency = spec.get("max_latency", 0)
            
            # warn if max latency exceeds 500 ns (potential bottleneck)
            if max_latency > 500:
                violations.append(
                    self._create_violation(
                        message=f"Slave {slave_name!r} maximum response latency {max_latency} ns is high.",
                        impact="Slow slave response may bottleneck the bus and block other masters.",
                        suggestion="Optimize the slave logic to reduce latency, or adjust the bus clock period.",
                        location=f"/bus/slaves/{slave_name}",
                        affected_nodes=[slave_name],
                    )
                )
        
        return violations


def register_rockchip_bus_rules(registry, soc_name: str) -> None:
    """Register Rockchip bus rules."""
    registry.register(BUS401SlaveAddressCollision(), soc_name)
    registry.register(BUS402FrequencyMismatch(), soc_name)
    registry.register(BUS403SlaveResponseTimeout(), soc_name)
