"""Architecture diagram generator.

``socc generate-diagram board.dts --format mermaid``
``socc generate-diagram board.dts --format plantuml``
``socc generate-diagram board.dts --format dot``

Translates the in-memory ``SoC`` model into a human-readable topology
diagram that can be embedded in GitHub READMEs, Obsidian notes,
lab reports, or architecture documents.

────────────────────────────────────────────────────────────────────────────
Diagram types
────────────────────────────────────────────────────────────────────────────
  power     — Regulator supply tree  (who powers whom)
  clock     — Clock distribution tree
  bus       — I²C / SPI / PCIe bus topology
  full      — All three merged into a single graph

────────────────────────────────────────────────────────────────────────────
Supported output formats
────────────────────────────────────────────────────────────────────────────
  mermaid   — Mermaid.js graph (embeddable in GitHub Markdown, Obsidian)
  plantuml  — PlantUML component diagram
  dot       — Graphviz DOT language

For academic lab reports: ``--format mermaid`` generates a code block you
can paste directly into a Markdown document.  GitHub renders it as an
interactive SVG with zero tooling required.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from socc.model import SoC


# ── ID sanitisation ───────────────────────────────────────────────────────────


def _mid(s: str) -> str:
    """Convert arbitrary string to a safe Mermaid node ID."""
    return re.sub(r"[^A-Za-z0-9_]", "_", s).strip("_") or "node"


def _did(s: str) -> str:
    """Convert to a safe DOT identifier."""
    return '"' + s.replace('"', '\\"') + '"'


def _plantuml_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s).strip("_") or "node"


# ── Mermaid generator ─────────────────────────────────────────────────────────


def _mermaid_power_tree(soc: SoC) -> str:
    """Render the power supply tree as a Mermaid flowchart."""
    lines = ["graph TD", f"    %% Power tree for {soc.name}"]
    tree = soc.power_tree

    for name, reg in sorted(tree.nodes.items()):
        mid_name = _mid(name)
        # Determine voltage label
        v_label = ""
        if hasattr(reg, "voltage_min") and hasattr(reg, "voltage_max"):
            vmin = getattr(reg, "voltage_min", 0)
            vmax = getattr(reg, "voltage_max", 0)
            if vmin > 0 and vmax > 0:
                if vmin == vmax:
                    v_label = f"{vmin:.2f}V"
                else:
                    v_label = f"{vmin:.2f}–{vmax:.2f}V"
            elif vmin > 0:
                v_label = f"{vmin:.2f}V"

        reg_type = getattr(reg, "type", "ldo").upper()
        node_label = f"{name}\\n({reg_type}{' · ' + v_label if v_label else ''})"
        lines.append(f"    {mid_name}[\"{node_label}\"]")

    # Edges from parent → child in power tree
    for name, children in sorted(tree.edges.items()):
        for child in children:
            lines.append(f"    {_mid(name)} --> {_mid(child)}")

    # Devices that consume a supply
    for dev_name, supplies in sorted(soc.device_supplies.items()):
        mid_dev = _mid("dev_" + dev_name)
        lines.append(f"    {mid_dev}([{dev_name}])")
        for supply in supplies:
            lines.append(f"    {_mid(supply)} -.->|powers| {mid_dev}")

    if len(lines) <= 2:
        lines.append("    %% (no power tree data extracted)")

    return "\n".join(lines)


def _mermaid_clock_tree(soc: SoC) -> str:
    """Render the clock distribution tree as a Mermaid flowchart."""
    lines = ["graph LR", f"    %% Clock tree for {soc.name}"]
    tree = soc.clock_tree

    rendered: Set[str] = set()
    for clk_name, clk in sorted(tree.clocks.items()):
        mid_c = _mid(clk_name)
        if mid_c in rendered:
            continue
        rendered.add(mid_c)
        freq = getattr(clk, "frequency", None) or getattr(clk, "rate", None)
        freq_label = ""
        if freq:
            if freq >= 1e9:
                freq_label = f" {freq/1e9:.2f}GHz"
            elif freq >= 1e6:
                freq_label = f" {freq/1e6:.0f}MHz"
            elif freq >= 1e3:
                freq_label = f" {freq/1e3:.0f}kHz"
            else:
                freq_label = f" {freq}Hz"
        parent = getattr(clk, "parent", None)
        label = f"{clk_name}{freq_label}"
        shape_open, shape_close = ("((", "))") if "pll" in clk_name.lower() else ("[", "]")
        lines.append(f'    {mid_c}{shape_open}"{label}"{shape_close}')
        if parent:
            lines.append(f"    {_mid(parent)} --> {mid_c}")

    # Device clock consumers
    for dev_name, clocks in sorted(soc.device_clocks.items()):
        mid_dev = _mid("dev_" + dev_name)
        lines.append(f"    {mid_dev}([{dev_name}])")
        for clk in clocks:
            lines.append(f"    {_mid(clk)} -.->|feeds| {mid_dev}")

    if len(lines) <= 2:
        lines.append("    %% (no clock tree data extracted)")

    return "\n".join(lines)


def _mermaid_bus_topology(soc: SoC) -> str:
    """Render bus (I2C/SPI/PCIe) topology as a Mermaid flowchart."""
    lines = ["graph TD", f"    %% Bus topology for {soc.name}"]

    _BUS_KEYWORDS = {
        "i2c": "I2C", "spi": "SPI", "uart": "UART",
        "pcie": "PCIe", "usb": "USB", "can": "CAN", "i3c": "I3C",
    }

    bus_devices: Dict[str, List[str]] = {}
    other_devices: List[str] = []

    for dev_name, node in sorted(soc.devices.items()):
        compat = node.properties.get("compatible", "")
        if isinstance(compat, (list, tuple)):
            compat = " ".join(str(x) for x in compat).lower()
        else:
            compat = str(compat).lower()
        name_lower = dev_name.lower()
        matched = False
        for kw, bus_label in _BUS_KEYWORDS.items():
            if kw in name_lower or kw in compat:
                bus_devices.setdefault(bus_label, []).append(dev_name)
                matched = True
                break
        if not matched:
            other_devices.append(dev_name)

    for bus_label, devices in sorted(bus_devices.items()):
        bus_id = _mid(f"bus_{bus_label}")
        lines.append(f'    {bus_id}["{bus_label} Bus"]')
        for dev in devices:
            addr = None
            reg = soc.devices[dev].properties.get("reg")
            if isinstance(reg, (list, tuple)) and reg:
                try:
                    addr = int(reg[0])
                except (TypeError, ValueError):
                    pass
            elif isinstance(reg, (int, float)):
                addr = int(reg)
            addr_str = f" @ 0x{addr:x}" if addr else ""
            dev_id = _mid(dev)
            lines.append(f'    {dev_id}["{dev}{addr_str}"]')
            lines.append(f"    {bus_id} --> {dev_id}")

    if len(lines) <= 2:
        lines.append("    %% (no bus topology data)")

    return "\n".join(lines)


def _mermaid_full(soc: SoC) -> str:
    """Merge power, clock and bus diagrams into a single annotated diagram."""
    return "\n\n".join([
        "```mermaid\n" + _mermaid_power_tree(soc) + "\n```",
        "```mermaid\n" + _mermaid_clock_tree(soc) + "\n```",
        "```mermaid\n" + _mermaid_bus_topology(soc) + "\n```",
    ])


# ── PlantUML generator ────────────────────────────────────────────────────────


def _plantuml_full(soc: SoC) -> str:
    lines = [
        "@startuml",
        f"' Auto-generated by socc generate-diagram for {soc.name}",
        "skinparam componentStyle rectangle",
        "",
        "package \"Power Tree\" {",
    ]
    tree = soc.power_tree
    for name, reg in sorted(tree.nodes.items()):
        vmin = getattr(reg, "voltage_min", 0)
        v_label = f" [{vmin:.2f}V]" if vmin else ""
        lines.append(f'  [{name}{v_label}] as {_plantuml_id(name)}')

    lines.append("}")
    lines.append("")
    lines.append("package \"Clock Tree\" {")

    for clk_name, clk in sorted(soc.clock_tree.clocks.items()):
        freq = getattr(clk, "frequency", None) or getattr(clk, "rate", None)
        f_label = ""
        if freq:
            f_label = f" [{freq/1e6:.0f}MHz]" if freq >= 1e6 else f" [{freq}Hz]"
        lines.append(f'  [{clk_name}{f_label}] as {_plantuml_id(clk_name)}')

    lines.append("}")
    lines.append("")
    lines.append("package \"Devices\" {")
    for dev_name in sorted(soc.devices):
        lines.append(f'  [{dev_name}] as {_plantuml_id("d_" + dev_name)}')
    lines.append("}")
    lines.append("")

    # Power edges
    for name, children in sorted(tree.edges.items()):
        for child in children:
            lines.append(
                f"{_plantuml_id(name)} --> {_plantuml_id(child)} : powers"
            )
    # Device supply edges
    for dev, supplies in sorted(soc.device_supplies.items()):
        for sup in supplies:
            lines.append(
                f"{_plantuml_id(sup)} ..> {_plantuml_id('d_' + dev)} : supply"
            )
    lines.append("")
    lines.append("@enduml")
    return "\n".join(lines)


# ── DOT / Graphviz generator ──────────────────────────────────────────────────


def _dot_full(soc: SoC) -> str:
    lines = [
        'digraph soc_topology {',
        '    rankdir=LR;',
        f'    label="{soc.name} — SoC Topology";',
        '    fontsize=14;',
        '    node [shape=box style=filled];',
        '',
        '    // Power supply nodes',
        '    subgraph cluster_power {',
        '        label="Power Tree";',
        '        color=orange;',
    ]
    for name in sorted(soc.power_tree.nodes):
        lines.append(f'        {_did(name)} [fillcolor=lightyellow];')
    lines.append("    }")

    lines.append("")
    lines.append("    // Clock nodes")
    lines.append("    subgraph cluster_clocks {")
    lines.append('        label="Clocks";')
    lines.append("        color=blue;")
    for clk_name in sorted(soc.clock_tree.clocks):
        lines.append(f'        {_did(clk_name)} [shape=ellipse fillcolor=lightblue];')
    lines.append("    }")

    lines.append("")
    lines.append("    // Device nodes")
    lines.append("    subgraph cluster_devices {")
    lines.append('        label="Devices";')
    lines.append("        color=green;")
    for dev in sorted(soc.devices):
        lines.append(f'        {_did(dev)} [fillcolor=lightgreen];')
    lines.append("    }")

    lines.append("")
    lines.append("    // Power tree edges")
    for name, children in sorted(soc.power_tree.edges.items()):
        for child in children:
            lines.append(f'    {_did(name)} -> {_did(child)} [color=orange];')

    lines.append("")
    lines.append("    // Clock edges")
    for clk_name, clk in sorted(soc.clock_tree.clocks.items()):
        parent = getattr(clk, "parent", None)
        if parent:
            lines.append(f'    {_did(parent)} -> {_did(clk_name)} [color=blue style=dashed];')

    lines.append("}")
    return "\n".join(lines)


# ── Main API ──────────────────────────────────────────────────────────────────


FORMATS = ("mermaid", "plantuml", "dot")
DIAGRAM_TYPES = ("power", "clock", "bus", "full")


def generate_diagram(soc: SoC, fmt: str = "mermaid",
                     diagram_type: str = "full") -> str:
    """Return the diagram as a string in the requested format.

    Parameters
    ----------
    soc:          Parsed SoC model.
    fmt:          One of 'mermaid', 'plantuml', 'dot'.
    diagram_type: One of 'power', 'clock', 'bus', 'full'.
    """
    fmt = fmt.lower()
    diagram_type = diagram_type.lower()

    if fmt == "mermaid":
        if diagram_type == "power":
            return "```mermaid\n" + _mermaid_power_tree(soc) + "\n```\n"
        if diagram_type == "clock":
            return "```mermaid\n" + _mermaid_clock_tree(soc) + "\n```\n"
        if diagram_type == "bus":
            return "```mermaid\n" + _mermaid_bus_topology(soc) + "\n```\n"
        return _mermaid_full(soc)

    if fmt == "plantuml":
        return _plantuml_full(soc)

    if fmt == "dot":
        return _dot_full(soc)

    raise ValueError(f"Unknown format {fmt!r}. Choose from: {FORMATS}")
