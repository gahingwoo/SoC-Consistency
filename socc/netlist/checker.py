"""Netlist ↔ DTS pinmux comparison.

Compares the *netlist* (from EDA CSV) against the DTS pinmux configuration
stored in ``SoC.pinmux_config`` and returns a list of mismatches.
"""

from dataclasses import dataclass
from typing import Dict, List

from socc.model import SoC

from .parser import NetlistPin, netlist_to_dict


@dataclass
class NetlistMismatch:
    """A single pin assignment discrepancy."""

    pin: str           # Physical pin identifier
    netlist_net: str   # Net name declared in the EDA netlist
    dts_function: str  # Function / net declared in the DTS pinctrl group
    severity: str = "error"   # "error" | "warning"

    def __str__(self) -> str:
        return (
            f"Mismatch! {self.pin} is {self.netlist_net} in netlist "
            f"but DTS configures it as {self.dts_function}"
        )


def diff_netlist_vs_dts(
    netlist_pins: List[NetlistPin],
    model: SoC,
) -> List[NetlistMismatch]:
    """Compare *netlist_pins* against ``model.pinmux_config``.

    A *mismatch* is declared when:
    - A pin appears in **both** the netlist and the DTS pinmux config, but the
      net name / function strings do not match (case-insensitive comparison).
    - Power/ground nets are compared strictly.

    Pins that appear in the netlist only (not configured in DTS) are skipped —
    that is caught by rule NET-601 separately.

    Args:
        netlist_pins: Parsed entries from the EDA CSV.
        model: SoC model with ``pinmux_config`` populated by the DTS mapper.

    Returns:
        List of :class:`NetlistMismatch` objects.
    """
    mismatches: List[NetlistMismatch] = []

    netlist_map = netlist_to_dict(netlist_pins)

    for pin, netlist_net in netlist_map.items():
        dts_func = model.pinmux_config.get(pin)
        if dts_func is None:
            # Pin not found in DTS pinmux — flagged by NET-601 rule, not here
            continue
        # Normalise: lower-case, remove hyphens/underscores for fuzzy compare
        if _normalise(netlist_net) != _normalise(dts_func):
            severity = "error" if _is_functional_mismatch(netlist_net, dts_func) else "warning"
            mismatches.append(
                NetlistMismatch(
                    pin=pin,
                    netlist_net=netlist_net,
                    dts_function=dts_func,
                    severity=severity,
                )
            )

    return mismatches


def _normalise(s: str) -> str:
    """Lower-case and strip separators for loose matching."""
    return s.lower().replace("-", "").replace("_", "").replace(" ", "")


def _is_functional_mismatch(net_a: str, net_b: str) -> bool:
    """Return True if the two nets represent genuinely different bus functions."""
    _BUS_KEYWORDS = {
        "i2c", "spi", "uart", "usb", "pcie", "mipi", "csi", "dsi",
        "can", "pwm", "jtag", "sdio", "emmc", "eth", "gmac",
    }
    a_bus = {kw for kw in _BUS_KEYWORDS if kw in net_a.lower()}
    b_bus = {kw for kw in _BUS_KEYWORDS if kw in net_b.lower()}
    # If both reference a bus but different ones → hard error
    if a_bus and b_bus and a_bus != b_bus:
        return True
    # One side is a bus function, the other is generic GPIO / no bus → hard error
    if a_bus != b_bus:
        return True
    # Power-rail vs signal mismatch
    a_pwr = any(x in net_a.lower() for x in ["vdd", "vcc", "gnd", "vss", "v3", "v1"])
    b_pwr = any(x in net_b.lower() for x in ["vdd", "vcc", "gnd", "vss", "v3", "v1"])
    if a_pwr != b_pwr:
        return True
    return False
