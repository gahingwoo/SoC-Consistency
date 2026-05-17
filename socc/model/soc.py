"""SoC top-level model."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .base import IRNode
from .clock import ClockTree
from .power import PowerTree
from .thermal import ThermalZone


@dataclass
class SoC:
    """SoC abstract model combining power, clock, and device subsystems."""

    name: str  # SoC name (e.g., "rk3588")
    power_tree: PowerTree = field(default_factory=PowerTree)
    clock_tree: ClockTree = field(default_factory=ClockTree)
    devices: Dict[str, IRNode] = field(default_factory=dict)  # device name -> IRNode
    device_supplies: Dict[str, List[str]] = field(default_factory=dict)  # device -> supply list
    device_clocks: Dict[str, List[str]] = field(default_factory=dict)  # device -> clock list
    pinmux_config: Dict[str, str] = field(default_factory=dict)  # pin_name -> function (from DTS pinctrl)
    thermal_zones: Dict[str, ThermalZone] = field(default_factory=dict)  # zone name -> ThermalZone

    @classmethod
    def from_ir(cls, ir_root: IRNode, soc_name: str) -> "SoC":
        """Build a SoC model from an IR tree (framework method)."""
        soc = cls(name=soc_name)
        # traverse IR tree — Schema-driven implementation goes here
        return soc

    def validate(self) -> List[str]:
        """Validate basic model consistency and return a list of error strings."""
        errors = []

        # check power tree cycles
        cycles = self.power_tree.detect_cycles()
        if cycles:
            errors.append(f"Cycle detected in power tree: {cycles}")

        # check clock tree cycles
        clock_cycles = self.clock_tree.detect_cycles()
        if clock_cycles:
            errors.append(f"Cycle detected in clock tree: {clock_cycles}")

        # check that required resources exist
        for device_name, supplies in self.device_supplies.items():
            for supply in supplies:
                if supply not in self.power_tree.nodes:
                    errors.append(f"Device {device_name} requires power supply {supply!r} which does not exist")

        for device_name, clocks in self.device_clocks.items():
            for clock in clocks:
                if clock not in self.clock_tree.clocks:
                    errors.append(f"Device {device_name} requires clock {clock!r} which does not exist")

        return errors
