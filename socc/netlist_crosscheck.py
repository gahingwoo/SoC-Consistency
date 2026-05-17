"""
EDA Netlist ↔ DTS Cross-Check — "PCB Dead-Short Detector"

Parses EDA netlists (KiCad .net Orcad-format, KiCad S-expr .kicad_sch,
or generic CSV pin→net tables) and cross-references every net/pin
against DTS pinctrl assignments.

The goal: catch the "I2C SDA is physically wired to GND on the PCB but
the DTS says it goes to GPIO4_C2" class of disaster *before* PCB
fabrication.

Supported input formats
-----------------------
* KiCad Orcad / legacy netlist  (.net)   — "(net (code N) (name "I2C3_SDA"))"
* KiCad netlist CSV export       (.csv)   — "RefDes,Pin,Net"
* Generic two-column TSV/CSV     (.tsv)   — "pin_name,net_name"

CLI entry:
  socc crosscheck board.dts schematic.net
  socc crosscheck board.dts schematic.csv --format csv
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from socc.model import SoC


# ─────────────────────────────────────────────────────────────────────────────
# Netlist data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NetlistPin:
    ref: str       # component reference, e.g. "U1"
    pin: str       # pin name or number, e.g. "GPIO4_C2" or "12"
    net: str       # net name, e.g. "I2C3_SDA" or "GND" or "VCC_3V3"


@dataclass
class NetlistModel:
    source_file: str
    fmt: str
    pins: List[NetlistPin] = field(default_factory=list)

    def nets_for_pin(self, pin_name: str) -> List[str]:
        """Return all nets connected to pins whose name contains pin_name."""
        pin_low = pin_name.lower()
        return [
            p.net for p in self.pins
            if pin_low in p.pin.lower() or pin_low in p.ref.lower()
        ]

    def pins_on_net(self, net_name: str) -> List[NetlistPin]:
        net_low = net_name.lower()
        return [p for p in self.pins if net_low in p.net.lower()]


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_kicad_net(text: str) -> List[NetlistPin]:
    """
    Parse KiCad Orcad-format .net file using a simple line/state approach.

    Example fragment:
      (net (code 14) (name "I2C3_SDA")
        (node (ref "U1") (pin "GPIO4_C2") (pinfunction "GPIO4_C2"))
      )
    """
    pins: List[NetlistPin] = []
    current_net: Optional[str] = None

    _name_re = re.compile(r'\(name\s+"?([^"\n)]+)"?\)')
    _node_re = re.compile(
        r'\(node\s+\(ref\s+"?([^"\n)]+)"?\)\s+\(pin\s+"?([^"\n)]+)"?\)'
    )

    for line in text.splitlines():
        # Track net name
        if "(net " in line:
            m = _name_re.search(line)
            if m:
                current_net = m.group(1).strip()
        elif "(name " in line and current_net is None:
            m = _name_re.search(line)
            if m:
                current_net = m.group(1).strip()
        # Collect node pins
        if current_net and "(node " in line:
            m = _node_re.search(line)
            if m:
                pins.append(NetlistPin(ref=m.group(1).strip(),
                                        pin=m.group(2).strip(),
                                        net=current_net))

    return pins


def _parse_csv(text: str) -> List[NetlistPin]:
    """
    Parse a CSV netlist.  Expects one of:
      RefDes, Pin, Net          (3-column KiCad export)
      PinName, Net              (2-column generic)
    """
    pins: List[NetlistPin] = []
    reader = csv.reader(io.StringIO(text))
    header_skipped = False
    for row in reader:
        row = [c.strip() for c in row]
        if not row:
            continue
        # Skip header rows
        if not header_skipped:
            if any(h.lower() in ("refdes", "ref", "pin", "net", "pinname") for h in row):
                header_skipped = True
                continue
        header_skipped = True  # treat first data row as data regardless
        if len(row) >= 3:
            pins.append(NetlistPin(ref=row[0], pin=row[1], net=row[2]))
        elif len(row) == 2:
            pins.append(NetlistPin(ref="", pin=row[0], net=row[1]))
    return pins


def parse_netlist(netlist_path: str, fmt: Optional[str] = None) -> NetlistModel:
    """
    Parse a netlist file and return a NetlistModel.

    fmt: "kicad" | "csv" | "auto" (default, detected from extension)
    """
    p = Path(netlist_path)
    text = p.read_text(errors="replace")

    if fmt is None:
        ext = p.suffix.lower()
        if ext in (".net",):
            fmt = "kicad"
        elif ext in (".csv", ".tsv"):
            fmt = "csv"
        else:
            # Try to auto-detect from content
            fmt = "kicad" if "(net " in text[:200] else "csv"

    if fmt == "kicad":
        pins = _parse_kicad_net(text)
    else:
        pins = _parse_csv(text)

    return NetlistModel(source_file=netlist_path, fmt=fmt, pins=pins)


# ─────────────────────────────────────────────────────────────────────────────
# Cross-reference logic
# ─────────────────────────────────────────────────────────────────────────────

# "Dangerous" net names that should never appear as a functional signal
_POWER_RAILS = frozenset({
    "gnd", "vss", "vcc", "vdd", "vcc_3v3", "vcc_1v8", "vcc_5v", "3v3", "1v8", "5v",
    "+3.3v", "+1.8v", "+5v", "ground",
})

# Known safe function nets (ignore during mismatch reporting)
_IGNORE_NETS = frozenset({"unconnected", "nc", "dnp", "reserved"})


def _extract_dts_pinctrl(soc: SoC) -> Dict[str, str]:
    """
    Return {logical_function → pin_name} from DTS pinmux config.

    e.g. {"i2c3_sda": "GPIO4_C2", "i2c3_scl": "GPIO4_C3"}

    This is best-effort; real pinmux config lives in a separate YAML/DTSI
    file.  We extract what we can from pinctrl-0 / pinctrl-names properties.
    """
    result: Dict[str, str] = {}
    for dev_name, node in soc.devices.items():
        pinctrl = node.properties.get("pinctrl-0") or node.properties.get("pinctrl-names")
        if not pinctrl:
            continue
        # pinctrl-0 may be a handle reference (string) or list of strings
        pin_str = " ".join(str(v) for v in (pinctrl if isinstance(pinctrl, list) else [pinctrl]))
        # Parse "gpio4_c2" or "GPIO4_C2" style references
        for m in re.finditer(r"gpio(\d+)_([a-h])(\d+)", pin_str, re.I):
            bank  = m.group(1)
            group = m.group(2).upper()
            num   = m.group(3)
            pin_name = f"GPIO{bank}_{group}{num}"
            func_key = f"{dev_name.split('@')[0]}_pin{num}"
            result[func_key] = pin_name
    return result


@dataclass
class CrossCheckResult:
    severity: str       # "FATAL" | "WARNING" | "INFO"
    rule_id: str
    dts_claim: str      # what the DTS says
    pcb_reality: str    # what the netlist says
    description: str
    fix: str = ""


def crosscheck_netlist_vs_dts(
    soc: SoC,
    netlist: NetlistModel,
) -> List[CrossCheckResult]:
    results: List[CrossCheckResult] = []

    # ── Check 1: Any safety-critical net grounded? ────────────────────────
    for pin in netlist.pins:
        net_low = pin.net.lower().strip()
        if net_low in _IGNORE_NETS:
            continue
        # Is this a functional signal (I2C, SPI, UART, etc.) tied to GND?
        is_functional = any(
            kw in net_low
            for kw in ("sda", "scl", "mosi", "miso", "sck", "tx", "rx", "cs",
                        "can_", "can-", "uart", "spi", "i2c")
        )
        is_power_rail = net_low in _POWER_RAILS or net_low.startswith("gnd")
        if is_functional and is_power_rail:
            results.append(CrossCheckResult(
                severity="FATAL",
                rule_id="PCB-001",
                dts_claim=f"Signal '{pin.net}' expected to carry data",
                pcb_reality=f"Pin {pin.ref}/{pin.pin} is connected to '{pin.net}' (power/ground rail)",
                description=(
                    f"Functional signal '{pin.net}' is shorted to a power rail. "
                    "Driving this pin from software will create a dead short."
                ),
                fix=(
                    f"Check the PCB layout around {pin.ref} pin {pin.pin}. "
                    "Verify no via accidentally connects to the ground plane."
                ),
            ))

    # ── Check 2: DTS pin assignments vs netlist net names ─────────────────
    dts_pins = _extract_dts_pinctrl(soc)
    for func, dts_pin in dts_pins.items():
        # Find this pin in the netlist
        matching = [p for p in netlist.pins if dts_pin.lower() in p.pin.lower()]
        if not matching:
            continue
        for m in matching:
            net_low = m.net.lower()
            if net_low in _IGNORE_NETS:
                continue
            # DTS says this pin is used for a functional signal; check that the
            # PCB net name is also a functional signal (not a power rail)
            if net_low in _POWER_RAILS or net_low.startswith("gnd"):
                results.append(CrossCheckResult(
                    severity="FATAL",
                    rule_id="PCB-002",
                    dts_claim=f"DTS: pin {dts_pin} = {func}",
                    pcb_reality=f"Netlist: {m.ref}/{m.pin} → Net '{m.net}' (power/ground)",
                    description=(
                        f"DTS claims {dts_pin} carries '{func}', but the PCB "
                        f"physically wires it to '{m.net}'."
                    ),
                    fix=(
                        f"Either correct the DTS pinctrl to use a different pin, "
                        f"or re-route the PCB to not ground {dts_pin}."
                    ),
                ))

    # ── Check 3: VCC supply net voltage consistency ────────────────────────
    for supply_name, reg in soc.power_tree.nodes.items():
        v_max = getattr(reg, "voltage_max", 0) or 0
        if not v_max:
            continue
        # Find PCB nets that could match this supply
        net_candidates = [
            p for p in netlist.pins
            if supply_name.lower().replace("_", "-") in p.net.lower()
            or p.net.lower().replace("-", "_") == supply_name.lower()
        ]
        for nc in net_candidates:
            # Check if the connected device pins look like IO pins with known limits
            if "1v8" in nc.net.lower() and v_max > 2.0:
                results.append(CrossCheckResult(
                    severity="FATAL",
                    rule_id="PCB-003",
                    dts_claim=f"DTS supply '{supply_name}' = {v_max:.2f}V",
                    pcb_reality=f"PCB net '{nc.net}' appears to be a 1.8V domain",
                    description=(
                        f"Supply {supply_name} at {v_max:.2f}V drives a PCB net "
                        f"'{nc.net}' that implies a 1.8V domain. Over-voltage will "
                        "destroy IO cells."
                    ),
                    fix=(
                        f"Check the DTS 'regulator-max-microvolt' for {supply_name}. "
                        "Use a 1.8V-rated LDO or level-shifter for this net."
                    ),
                ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────────

_COLORS = {
    "FATAL":   "\033[1;31m",
    "WARNING": "\033[1;33m",
    "INFO":    "\033[1;36m",
    "RESET":   "\033[0m",
}


def render_crosscheck_report(
    results: List[CrossCheckResult],
    netlist_path: str,
    dts_path: str,
    use_color: bool = True,
) -> str:
    lines: List[str] = []

    def c(sev: str, text: str) -> str:
        return f"{_COLORS.get(sev, '')}{text}{_COLORS['RESET']}" if use_color else text

    lines.append("=" * 70)
    lines.append("  SOCC EDA-DTS CROSSCHECK")
    lines.append(f"  DTS     : {dts_path}")
    lines.append(f"  Netlist : {netlist_path}")
    lines.append("=" * 70)

    fatals   = [r for r in results if r.severity == "FATAL"]
    warnings = [r for r in results if r.severity == "WARNING"]
    infos    = [r for r in results if r.severity == "INFO"]

    if not results:
        lines.append(c("INFO", "\n[✓] No netlist/DTS mismatches detected."))
        lines.append("    Note: cross-check coverage depends on how completely the")
        lines.append("    netlist pin names match DTS GPIO references.")
    else:
        for r in results:
            lines.append("")
            lines.append(c(r.severity, f"[{r.severity}] [{r.rule_id}]"))
            lines.append(f"  DTS says  : {r.dts_claim}")
            lines.append(f"  PCB shows : {r.pcb_reality}")
            lines.append(f"  Problem   : {r.description}")
            if r.fix:
                lines.append(f"  Fix       : {r.fix}")

    lines.append("")
    lines.append(
        f"Summary: {len(fatals)} fatal, {len(warnings)} warning(s), {len(infos)} info."
    )
    return "\n".join(lines)
