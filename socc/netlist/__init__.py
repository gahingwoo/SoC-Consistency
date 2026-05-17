"""Netlist cross-checking module.

Parses EDA CSV pin-assignment reports (KiCad / Altium) and compares them
against the DTS pinmux configuration embedded in the SoC model.
"""

from .parser import parse_netlist_csv, NetlistPin
from .checker import diff_netlist_vs_dts, NetlistMismatch

__all__ = ["parse_netlist_csv", "NetlistPin", "diff_netlist_vs_dts", "NetlistMismatch"]
