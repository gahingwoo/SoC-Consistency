"""Netlist cross-check rules (NET-6xx series).

These rules compare the DTS pinmux configuration against an external EDA
netlist.  They are *only* active when a netlist CSV is provided to the
checker via ``context.metadata["netlist_pins"]``.
"""

from typing import List

from socc.model import SoC, Violation
from socc.netlist.parser import NetlistPin, netlist_to_dict
from socc.netlist.checker import diff_netlist_vs_dts

from ..base import BaseRule, CheckContext


class NET601PinmuxMismatch(BaseRule):
    """NET-601: DTS pinmux function does not match EDA netlist assignment."""

    code = "NET-601"
    name = "Pinmux Netlist Mismatch"
    description = (
        "Compares DTS pinctrl group assignments against the EDA board netlist. "
        "Reports any pin whose function declared in the DTS differs from the "
        "net name recorded in the schematic CSV export."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        netlist_pins: List[NetlistPin] = context.metadata.get("netlist_pins", [])
        if not netlist_pins:
            return []  # netlist not provided — skip silently

        violations: List[Violation] = []
        mismatches = diff_netlist_vs_dts(netlist_pins, model)

        for mm in mismatches:
            violations.append(
                self._create_violation(
                    message=(
                        f"Mismatch! {mm.pin} is {mm.netlist_net!r} in the netlist "
                        f"but DTS configures it as {mm.dts_function!r}"
                    ),
                    impact=(
                        "Pin assigned to wrong function — may cause bus contention, "
                        "data corruption, or hardware damage."
                    ),
                    suggestion=(
                        f"Either update the DTS pinctrl group to use function "
                        f"{mm.netlist_net!r}, or correct the schematic net name."
                    ),
                    location=f"/pinctrl/{mm.pin}",
                    affected_nodes=[mm.pin],
                    severity=mm.severity,
                )
            )

        return violations


class NET602UnclaimedNetlistPin(BaseRule):
    """NET-602: Netlist pin has no corresponding DTS pinctrl assignment."""

    code = "NET-602"
    name = "Unclaimed Netlist Pin"
    description = (
        "Reports pins present in the EDA netlist that have no matching entry "
        "in the DTS pinctrl configuration.  This may indicate a missing pinmux "
        "group or an unintentionally unconfigured pad."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        netlist_pins: List[NetlistPin] = context.metadata.get("netlist_pins", [])
        if not netlist_pins:
            return []

        violations: List[Violation] = []
        netlist_map = netlist_to_dict(netlist_pins)

        # Filter out pure power/ground pads — those rarely appear in pinctrl
        _PWR_PREFIXES = ("VDD", "VCC", "GND", "VSS", "AVDD", "DVDD", "AGND", "DGND")

        for pin, net in netlist_map.items():
            net_upper = net.upper()
            if any(net_upper.startswith(p) for p in _PWR_PREFIXES):
                continue  # skip power/ground balls
            if pin not in model.pinmux_config:
                violations.append(
                    self._create_violation(
                        message=(
                            f"Pin {pin!r} (net {net!r}) appears in the netlist "
                            f"but has no pinctrl entry in the DTS."
                        ),
                        impact="Pad left in reset state — may float or drive unexpected logic.",
                        suggestion=(
                            f"Add a pinctrl group for {pin!r} with the correct "
                            f"function, pull, and drive-strength settings."
                        ),
                        location=f"/pinctrl/{pin}",
                        affected_nodes=[pin],
                    )
                )

        return violations


def register_netlist_rules(registry, soc_name: str = "common") -> None:
    """Register netlist cross-check rules into *registry*."""
    registry.register(NET601PinmuxMismatch(), soc_name)
    registry.register(NET602UnclaimedNetlistPin(), soc_name)


__all__ = [
    "register_netlist_rules",
    "NET601PinmuxMismatch",
    "NET602UnclaimedNetlistPin",
]
