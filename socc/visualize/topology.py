"""Interactive hardware topology graph renderer.

Generates a self-contained HTML page using pyvis (vis.js under the hood)
that shows the full power tree, clock tree, and device graph of a parsed
SoC model in an interactive, zoomable, draggable node-graph view.

Node color convention
─────────────────────
  PMIC / root regulator  →  #e74c3c  (red)
  Regulator (DCDC/LDO)   →  #e67e22  (orange)
  Clock PLL / provider   →  #2980b9  (blue)
  Clock signal           →  #5dade2  (light blue)
  Device                 →  #27ae60  (green)

Edge color convention
─────────────────────
  Power supply edge      →  #e74c3c
  Clock feed edge        →  #2980b9
  Device-power edge      →  #95a5a6
  Violation edge         →  #ff0000  (red, dashed, animated)

Depends on: pyvis >= 0.3.2  (installed via pip install pyvis)
"""

from __future__ import annotations

import html as _html
import json
import textwrap
from pathlib import Path
from typing import List, Optional

from socc.model import SoC, Violation

# pyvis import is deferred to give a clean error if not installed
_PYVIS_AVAILABLE: bool | None = None


def _ensure_pyvis() -> None:
    global _PYVIS_AVAILABLE
    if _PYVIS_AVAILABLE is None:
        try:
            import pyvis  # noqa: F401
            _PYVIS_AVAILABLE = True
        except ImportError:
            _PYVIS_AVAILABLE = False
    if not _PYVIS_AVAILABLE:
        raise ImportError(
            "pyvis is required for topology graph export.  "
            "Install it with:  pip install pyvis"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Color palette
# ─────────────────────────────────────────────────────────────────────────────

_COLOR = {
    "pmic":        "#c0392b",   # deep red — root power rails
    "dcdc":        "#e67e22",   # orange — DCDC regulators
    "ldo":         "#f39c12",   # amber — LDO regulators
    "switch":      "#f1c40f",   # yellow — load switches
    "fixed":       "#e74c3c",   # red — fixed power rails
    "clock_pll":   "#1a5276",   # dark blue — PLL providers
    "clock_sig":   "#2e86c1",   # medium blue — clock signals
    "device":      "#1e8449",   # dark green — devices
    "device_err":  "#e74c3c",   # red — devices with violations
    "violation":   "#ff0000",   # pure red — violation edge
    "power_edge":  "#e74c3c",
    "clock_edge":  "#2980b9",
    "dev_edge":    "#7f8c8d",
}

_BORDER_VIOLATION = "#ff0000"
_BORDER_DEFAULT   = "#2c3e50"

_NODE_SIZE = {
    "pmic":      40,
    "dcdc":      28,
    "ldo":       22,
    "switch":    18,
    "fixed":     24,
    "clock_pll": 26,
    "clock_sig": 16,
    "device":    20,
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _node_id(prefix: str, name: str) -> str:
    """Create a unique node ID without spaces."""
    return f"{prefix}::{name}".replace(" ", "_")


def _voltage_label(reg) -> str:
    """Return a short voltage string like '1.8V' or '0.8–0.9V'."""
    v_min = getattr(reg, "voltage_min", 0)
    v_max = getattr(reg, "voltage_max", 0)
    if v_min == v_max:
        return f"{v_min:.1f}V"
    return f"{v_min:.1f}–{v_max:.1f}V"


def _reg_type(reg) -> str:
    t = getattr(reg, "type", "fixed")
    if t in _COLOR:
        return t
    return "fixed"


def _tooltip(lines: list) -> str:
    """Build a plain-text tooltip string."""
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main export function
# ─────────────────────────────────────────────────────────────────────────────

def render_topology_html(
    model: SoC,
    output_path: str,
    violations: Optional[List[Violation]] = None,
    title: Optional[str] = None,
) -> str:
    """Build an interactive topology graph and write it to *output_path*.

    Parameters
    ----------
    model:
        Parsed SoC model (power tree + clock tree + devices).
    output_path:
        Destination .html file path.
    violations:
        Optional list of violations from the checker; affected nodes will
        be highlighted in red and violating edges will flash.
    title:
        Optional page title shown at the top of the HTML.

    Returns
    -------
    str
        Absolute path to the written HTML file.
    """
    _ensure_pyvis()
    from pyvis.network import Network

    violations = violations or []
    title = title or f"SoC Topology — {model.name}"

    # Collect violated node names for highlighting
    violated_nodes: set[str] = set()
    for v in violations:
        violated_nodes.update(getattr(v, "affected_nodes", []))

    net = Network(
        height="92vh",
        width="100%",
        bgcolor="#1a1a2e",
        font_color="#ecf0f1",
        directed=True,
        notebook=False,
    )
    net.set_options(json.dumps({
        "physics": {
            "enabled": True,
            "solver": "forceAtlas2Based",
            "forceAtlas2Based": {
                "gravitationalConstant": -80,
                "springLength": 160,
                "springConstant": 0.04,
                "damping": 0.3,
            },
            "stabilization": {"iterations": 150},
        },
        "interaction": {
            "hover": True,
            "tooltipDelay": 100,
            "navigationButtons": True,
            "keyboard": True,
        },
        "edges": {
            "smooth": {"type": "dynamic"},
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.8}},
            "color": {"inherit": False},
            "width": 1.5,
        },
        "nodes": {
            "borderWidth": 2,
            "borderWidthSelected": 4,
            "font": {"size": 12, "color": "#ecf0f1"},
        },
    }))

    # ── Power tree nodes ──────────────────────────────────────────────────
    for reg_name, reg in model.power_tree.nodes.items():
        rtype = _reg_type(reg)
        # root regulators (no parent) are rendered as PMIC
        parents = model.power_tree.reverse_edges.get(reg_name, [])
        if not parents:
            rtype = "pmic"

        is_violated = reg_name in violated_nodes
        color = _COLOR.get(rtype, _COLOR["fixed"])
        border = _BORDER_VIOLATION if is_violated else _BORDER_DEFAULT
        size = _NODE_SIZE.get(rtype, 20)

        max_i = getattr(reg, "max_current_ma", 0)
        tip = _tooltip([
            f"⚡ {reg_name}",
            f"Type : {rtype.upper()}",
            f"Voltage : {_voltage_label(reg)}",
            f"Max current : {max_i} mA" if max_i else "Max current : unspecified",
            f"Consumers : {', '.join(reg.consumers) or 'none'}",
            f"Seq order : {reg.sequence_order}",
        ])
        if is_violated:
            tip += "\n⚠ VIOLATION — see report"

        net.add_node(
            _node_id("pwr", reg_name),
            label=reg_name,
            color={"background": color, "border": border},
            size=size,
            shape="ellipse" if rtype == "pmic" else "dot",
            title=tip,
            group="power",
        )

    # ── Power tree edges ──────────────────────────────────────────────────
    for parent_name, children in model.power_tree.edges.items():
        for child_name in children:
            # is this edge part of a violation?
            edge_violated = (
                parent_name in violated_nodes and child_name in violated_nodes
            )
            net.add_edge(
                _node_id("pwr", parent_name),
                _node_id("pwr", child_name),
                color=_BORDER_VIOLATION if edge_violated else _COLOR["power_edge"],
                dashes=edge_violated,
                width=3 if edge_violated else 1.5,
                title=f"{parent_name} → {child_name} (power)",
            )

    # ── Clock tree: PLL providers ─────────────────────────────────────────
    for prov_name, prov in model.clock_tree.providers.items():
        is_violated = prov_name in violated_nodes
        tip = _tooltip([
            f"🕐 {prov_name}",
            f"Type : {prov.type}",
            f"Outputs : {', '.join(prov.outputs) or 'none'}",
        ])
        net.add_node(
            _node_id("cprov", prov_name),
            label=prov_name,
            color={
                "background": _COLOR["clock_pll"],
                "border": _BORDER_VIOLATION if is_violated else "#1a5276",
            },
            size=_NODE_SIZE["clock_pll"],
            shape="diamond",
            title=tip,
            group="clock_provider",
        )

    # ── Clock tree: clock signals ─────────────────────────────────────────
    for clk_name, clk in model.clock_tree.clocks.items():
        is_violated = clk_name in violated_nodes
        freq_mhz = clk.rate / 1_000_000 if clk.rate else 0
        tip = _tooltip([
            f"⏱ {clk_name}",
            f"Rate : {freq_mhz:.1f} MHz",
            f"Provider : {clk.provider}",
            f"Parent : {clk.parent or 'root'}",
            f"Consumers : {', '.join(clk.consumers) or 'none'}",
        ])
        if is_violated:
            tip += "\n⚠ VIOLATION — see report"

        net.add_node(
            _node_id("clk", clk_name),
            label=f"{clk_name}\n{freq_mhz:.0f}MHz",
            color={
                "background": _COLOR["clock_sig"],
                "border": _BORDER_VIOLATION if is_violated else "#1a5276",
            },
            size=_NODE_SIZE["clock_sig"],
            shape="triangleDown",
            title=tip,
            group="clock",
        )
        # edge: provider → clock signal
        prov_id = _node_id("cprov", clk.provider)
        if clk.provider in model.clock_tree.providers:
            net.add_edge(
                prov_id,
                _node_id("clk", clk_name),
                color=_COLOR["clock_edge"],
                width=1,
                dashes=False,
                title=f"{clk.provider} → {clk_name} (clock)",
            )
        # edge: parent clock → child clock
        if clk.parent and clk.parent in model.clock_tree.clocks:
            net.add_edge(
                _node_id("clk", clk.parent),
                _node_id("clk", clk_name),
                color=_COLOR["clock_edge"],
                width=1,
                dashes=True,
                title=f"{clk.parent} → {clk_name} (clock parent)",
            )

    # ── Device nodes ──────────────────────────────────────────────────────
    for dev_name, dev_node in model.devices.items():
        is_violated = dev_name in violated_nodes
        supplies = model.device_supplies.get(dev_name, [])
        clocks = model.device_clocks.get(dev_name, [])
        compat = dev_node.get_property("compatible", "unknown")
        tip = _tooltip([
            f"🔧 {dev_name}",
            f"Compatible : {compat}",
            f"Supplies : {', '.join(supplies) or 'none'}",
            f"Clocks : {', '.join(clocks) or 'none'}",
        ])
        if is_violated:
            tip += "\n⚠ VIOLATION — see report"

        net.add_node(
            _node_id("dev", dev_name),
            label=dev_name,
            color={
                "background": _COLOR["device_err"] if is_violated else _COLOR["device"],
                "border": _BORDER_VIOLATION if is_violated else "#1e8449",
            },
            size=_NODE_SIZE["device"],
            shape="box",
            title=tip,
            group="device",
        )
        # edges: device ← power supplies
        for supply in supplies:
            if supply in model.power_tree.nodes:
                net.add_edge(
                    _node_id("pwr", supply),
                    _node_id("dev", dev_name),
                    color=_COLOR["dev_edge"],
                    width=1,
                    dashes=False,
                    title=f"{supply} powers {dev_name}",
                )
        # edges: device ← clocks
        for clk_name in clocks:
            if clk_name in model.clock_tree.clocks:
                net.add_edge(
                    _node_id("clk", clk_name),
                    _node_id("dev", dev_name),
                    color=_COLOR["clock_edge"],
                    width=1,
                    dashes=True,
                    title=f"{clk_name} feeds {dev_name}",
                )

    # ── Violation flash overlay (JS injected into HTML) ───────────────────
    flash_js = ""
    if violations:
        v_msgs = [
            _html.escape(f"[{v.code}] {v.message[:120]}") for v in violations[:20]
        ]
        v_list_html = "".join(
            f'<li style="margin:4px 0;color:#e74c3c;">{m}</li>'
            for m in v_msgs
        )
        flash_js = textwrap.dedent(f"""
            <div id="violation-panel" style="
                position:fixed; top:8px; right:8px; width:380px;
                background:#1a1a2e; border:2px solid #e74c3c;
                border-radius:8px; padding:12px; z-index:9999;
                font-family:monospace; font-size:12px; color:#ecf0f1;
                max-height:60vh; overflow-y:auto;">
              <b style="color:#e74c3c;">⚠ Violations ({len(violations)})</b>
              <ul style="margin:8px 0 0 0;padding-left:16px;">
                {v_list_html}
              </ul>
              <button onclick="this.parentElement.style.display='none'"
                style="margin-top:8px;background:#e74c3c;border:none;
                       color:white;padding:4px 10px;border-radius:4px;cursor:pointer;">
                Dismiss
              </button>
            </div>
        """)

    # Legend HTML injected via custom template
    legend_html = textwrap.dedent("""
        <div style="
            position:fixed; bottom:8px; left:8px;
            background:rgba(26,26,46,0.92); border:1px solid #444;
            border-radius:8px; padding:10px 14px; font-family:monospace;
            font-size:11px; color:#ecf0f1; z-index:9998;">
          <b>Legend</b><br>
          <span style="color:#c0392b;">●</span> PMIC / Root supply&nbsp;&nbsp;
          <span style="color:#e67e22;">●</span> DCDC&nbsp;&nbsp;
          <span style="color:#f39c12;">●</span> LDO<br>
          <span style="color:#1a5276;">◆</span> PLL provider&nbsp;&nbsp;
          <span style="color:#2e86c1;">▼</span> Clock signal<br>
          <span style="color:#1e8449;">■</span> Device&nbsp;&nbsp;
          <span style="color:#e74c3c;">■</span> Device with violation<br>
          <span style="color:#e74c3c;">—</span> Power edge&nbsp;&nbsp;
          <span style="color:#2980b9;">—</span> Clock edge<br>
          <span style="color:#ff0000;">- -</span> Violation edge
        </div>
    """)

    # Write the network HTML; pyvis generates a full standalone HTML file
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(out))

    # Inject title, legend, and violation panel into the generated HTML
    raw = out.read_text(encoding="utf-8")

    # Replace default title
    raw = raw.replace("<title>", f"<title>{_html.escape(title)} — ", 1)

    # Inject custom CSS + panels before </body>
    inject = textwrap.dedent(f"""
        <!-- socc topology injected styles -->
        <style>
          body {{ background: #1a1a2e !important; }}
          #mynetwork {{ border: none !important; background: #1a1a2e !important; }}
          h1.card-title {{ color: #ecf0f1; font-family: monospace;
                           font-size: 16px; margin: 4px 8px; }}
        </style>
        <h1 class="card-title">{_html.escape(title)}</h1>
        {flash_js}
        {legend_html}
    """)
    raw = raw.replace("</body>", inject + "\n</body>", 1)
    out.write_text(raw, encoding="utf-8")

    return str(out)


__all__ = ["render_topology_html"]
