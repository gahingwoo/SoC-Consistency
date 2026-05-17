"""
HTML Architecture Report Generator — "socc-ui" Visual Architect

Generates a self-contained, single-file HTML report with:
  * Board summary card (SoC name, device count, issue count)
  * Power tree visualisation (collapsible tree)
  * Clock domain table
  * Peripheral register map table
  * Embedded Mermaid.js diagrams (no external CDN required for text)
  * Full socc check results (colour-coded by severity)

Designed to be dropped into a Yocto build artefacts folder, attached to
a GitLab/GitHub MR, or opened on any device with a browser — no server
needed.

CLI entry:
  socc generate-report board.dts -o report.html
  socc generate-report board.dts --format html
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from socc.model import SoC


# ─────────────────────────────────────────────────────────────────────────────
# Data collection from SoC model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReportData:
    dts_path: str
    soc_name: str
    generated_at: str
    device_count: int
    enabled_count: int
    power_nodes: List[Dict[str, Any]]
    clock_nodes: List[Dict[str, Any]]
    peripherals: List[Dict[str, Any]]
    violations: List[Dict[str, Any]] = field(default_factory=list)
    mermaid_power: str = ""
    mermaid_clock: str = ""


def _collect_report_data(
    soc: SoC,
    dts_path: str = "board.dts",
    violations: Optional[List[Dict[str, Any]]] = None,
) -> ReportData:
    # Power nodes
    power_nodes = []
    for name, reg in soc.power_tree.nodes.items():
        v_min = getattr(reg, "voltage_min", 0) or 0
        v_max = getattr(reg, "voltage_max", 0) or 0
        # Convert µV to V if value looks like µV (> 1000)
        if v_max > 1000:
            v_min /= 1_000_000
            v_max /= 1_000_000
        power_nodes.append({
            "name": name,
            "type": getattr(reg, "type", "regulator"),
            "v_min": f"{v_min:.3f}",
            "v_max": f"{v_max:.3f}",
            "parent": getattr(reg, "parent", "") or "",
            "consumers": getattr(reg, "consumers", []) or [],
        })

    # Clock nodes
    clock_nodes = []
    for name, clk in soc.clock_tree.clocks.items():
        hz = getattr(clk, "rate", None) or getattr(clk, "frequency", 0) or 0
        parent = getattr(clk, "parent", "") or ""
        clock_nodes.append({
            "name": name,
            "hz": hz,
            "mhz": f"{hz/1e6:.1f}" if hz else "?",
            "parent": parent,
        })

    # Peripherals
    peripherals = []
    for dev_name, node in sorted(soc.devices.items()):
        status = node.properties.get("status", "okay")
        compat = node.properties.get("compatible") or []
        if isinstance(compat, str):
            compat = [compat]
        reg = node.properties.get("reg")
        base = ""
        size = ""
        if isinstance(reg, list) and len(reg) >= 2:
            base = f"0x{reg[0]:08X}" if isinstance(reg[0], int) else str(reg[0])
            size = f"0x{reg[1]:08X}" if isinstance(reg[1], int) else str(reg[1])
        elif isinstance(reg, int):
            base = f"0x{reg:08X}"
        irq = node.properties.get("interrupts") or ""
        peripherals.append({
            "name": dev_name,
            "path": node.path,
            "status": status,
            "compat": ", ".join(compat[:2]),
            "base": base,
            "size": size,
            "irq": str(irq)[:30] if irq else "",
        })

    # Count enabled
    enabled = sum(1 for p in peripherals if p["status"] in ("okay", "ok"))

    # Mermaid diagrams (simplified)
    mermaid_power = _mermaid_power(soc)
    mermaid_clock = _mermaid_clock(soc)

    return ReportData(
        dts_path=dts_path,
        soc_name=soc.name,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        device_count=len(peripherals),
        enabled_count=enabled,
        power_nodes=power_nodes,
        clock_nodes=clock_nodes,
        peripherals=peripherals,
        violations=violations or [],
        mermaid_power=mermaid_power,
        mermaid_clock=mermaid_clock,
    )


def _mermaid_power(soc: SoC) -> str:
    lines = ["graph TD"]
    for name, reg in list(soc.power_tree.nodes.items())[:30]:
        safe = name.replace("-", "_").replace(",", "_").replace(".", "_")
        parent = getattr(reg, "parent", None)
        if parent:
            psafe = parent.replace("-", "_").replace(",", "_").replace(".", "_")
            lines.append(f"  {psafe}[{parent}] --> {safe}[{name}]")
        else:
            lines.append(f"  {safe}[{name}]")
    return "\n".join(lines)


def _mermaid_clock(soc: SoC) -> str:
    lines = ["graph LR"]
    for name, clk in list(soc.clock_tree.clocks.items())[:30]:
        safe = name.replace("-", "_").replace(",", "_").replace(".", "_")
        parent = getattr(clk, "parent", None)
        if parent:
            psafe = parent.replace("-", "_").replace(",", "_").replace(".", "_")
            hz = getattr(clk, "rate", None) or getattr(clk, "frequency", 0) or 0
            label = f"{name}" + (f"\\n{hz/1e6:.0f}MHz" if hz else "")
            lines.append(f"  {psafe} --> {safe}[{label}]")
        else:
            lines.append(f"  {safe}([{name}])")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# HTML template
# ─────────────────────────────────────────────────────────────────────────────

_SEV_COLOR = {
    "fatal":    "#ff4444",
    "critical": "#ff8800",
    "warning":  "#ffcc00",
    "info":     "#44aaff",
    "error":    "#ff4444",
}

_STATUS_BADGE = {
    "okay": '<span class="badge ok">okay</span>',
    "ok":   '<span class="badge ok">okay</span>',
}


def _h(s: Any) -> str:
    return html.escape(str(s))


def _violation_rows(violations: List[Dict[str, Any]]) -> str:
    if not violations:
        return '<tr><td colspan="4" style="text-align:center;color:#888">No violations found ✓</td></tr>'
    rows = []
    for v in violations:
        sev = str(v.get("severity", "info")).lower()
        color = _SEV_COLOR.get(sev, "#aaa")
        rows.append(
            f'<tr>'
            f'<td><span class="badge" style="background:{color}">{_h(v.get("severity","?"))}</span></td>'
            f'<td>{_h(v.get("node_path", v.get("path", "")))}</td>'
            f'<td>{_h(v.get("description", v.get("message", "")))}</td>'
            f'<td>{_h(v.get("fix", v.get("suggestion", "")))}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def _peripheral_rows(peripherals: List[Dict[str, Any]]) -> str:
    rows = []
    for p in peripherals:
        status = p["status"]
        badge = _STATUS_BADGE.get(status, f'<span class="badge dis">{_h(status)}</span>')
        rows.append(
            f'<tr>'
            f'<td><code>{_h(p["name"])}</code></td>'
            f'<td>{badge}</td>'
            f'<td><code>{_h(p["base"])}</code></td>'
            f'<td><code>{_h(p["size"])}</code></td>'
            f'<td><small>{_h(p["compat"])}</small></td>'
            f'<td><small>{_h(p["irq"])}</small></td>'
            f'</tr>'
        )
    return "\n".join(rows)


def _power_rows(power_nodes: List[Dict[str, Any]]) -> str:
    rows = []
    for n in power_nodes:
        rows.append(
            f'<tr>'
            f'<td><code>{_h(n["name"])}</code></td>'
            f'<td>{_h(n["v_min"])}</td>'
            f'<td>{_h(n["v_max"])}</td>'
            f'<td><code>{_h(n["parent"])}</code></td>'
            f'<td><small>{_h(", ".join(n["consumers"][:4]))}</small></td>'
            f'</tr>'
        )
    return "\n".join(rows)


def _clock_rows(clock_nodes: List[Dict[str, Any]]) -> str:
    rows = []
    for n in sorted(clock_nodes, key=lambda x: -x["hz"])[:40]:
        rows.append(
            f'<tr>'
            f'<td><code>{_h(n["name"])}</code></td>'
            f'<td>{_h(n["mhz"])} MHz</td>'
            f'<td><code>{_h(n["parent"])}</code></td>'
            f'</tr>'
        )
    return "\n".join(rows)


def generate_html_report(
    soc: SoC,
    dts_path: str = "board.dts",
    violations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    data = _collect_report_data(soc, dts_path, violations)
    vcount = len(data.violations)
    fatal = sum(1 for v in data.violations if str(v.get("severity","")).lower() in ("fatal","error","critical"))

    header_color = "#22c55e" if fatal == 0 else "#ef4444"
    result_text  = "PASS" if fatal == 0 else f"FAIL — {fatal} critical/fatal violation(s)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SoC-Consistency Report — {_h(data.soc_name)}</title>
<style>
  :root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); }}
  header {{ background: {header_color}22; border-bottom: 3px solid {header_color}; padding: 1.5rem 2rem; }}
  header h1 {{ font-size: 1.6rem; color: {header_color}; }}
  header .meta {{ color: var(--muted); font-size: 0.85rem; margin-top: 0.3rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; padding: 1.5rem 2rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }}
  .card h3 {{ font-size: 0.75rem; text-transform: uppercase; color: var(--muted); margin-bottom: 0.5rem; }}
  .card .val {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
  section {{ padding: 0 2rem 2rem; }}
  h2 {{ font-size: 1.1rem; color: var(--accent); margin: 1.5rem 0 0.75rem; border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ background: var(--surface); color: var(--muted); text-align: left; padding: 0.5rem 0.6rem; font-weight: 600; position: sticky; top: 0; }}
  td {{ padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tr:hover td {{ background: #ffffff08; }}
  code {{ font-family: 'Fira Code', monospace; font-size: 0.82em; background: #ffffff12; padding: 1px 4px; border-radius: 3px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
  .badge.ok  {{ background: #22c55e33; color: #22c55e; }}
  .badge.dis {{ background: #94a3b833; color: #94a3b8; }}
  .mermaid-wrap {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; overflow-x: auto; }}
  pre.mermaid {{ font-size: 0.8rem; color: var(--text); }}
  footer {{ text-align: center; padding: 1rem; color: var(--muted); font-size: 0.8rem; border-top: 1px solid var(--border); }}
  @media (max-width: 600px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>SoC-Consistency Report — {_h(data.soc_name)}</h1>
  <div class="meta">
    DTS: <code>{_h(data.dts_path)}</code> &nbsp;·&nbsp;
    Generated: {_h(data.generated_at)} &nbsp;·&nbsp;
    Result: <strong style="color:{header_color}">{_h(result_text)}</strong>
  </div>
</header>

<div class="grid">
  <div class="card"><h3>Total Devices</h3><div class="val">{data.device_count}</div></div>
  <div class="card"><h3>Enabled</h3><div class="val">{data.enabled_count}</div></div>
  <div class="card"><h3>Power Rails</h3><div class="val">{len(data.power_nodes)}</div></div>
  <div class="card"><h3>Clock Domains</h3><div class="val">{len(data.clock_nodes)}</div></div>
  <div class="card"><h3>Violations</h3><div class="val" style="color:{header_color}">{vcount}</div></div>
</div>

<section>

<h2>Violations</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr><th>Severity</th><th>Node</th><th>Description</th><th>Fix</th></tr></thead>
  <tbody>
    {_violation_rows(data.violations)}
  </tbody>
</table>
</div>

<h2>Peripheral Register Map</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr><th>Device</th><th>Status</th><th>Base Addr</th><th>Size</th><th>Compatible</th><th>IRQ</th></tr></thead>
  <tbody>
    {_peripheral_rows(data.peripherals)}
  </tbody>
</table>
</div>

<h2>Power Domains</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr><th>Supply</th><th>Min (V)</th><th>Max (V)</th><th>Parent</th><th>Consumers</th></tr></thead>
  <tbody>
    {_power_rows(data.power_nodes)}
  </tbody>
</table>
</div>

<h2>Clock Tree (top 40 by frequency)</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr><th>Clock</th><th>Frequency</th><th>Parent</th></tr></thead>
  <tbody>
    {_clock_rows(data.clock_nodes)}
  </tbody>
</table>
</div>

<h2>Power Tree Diagram (Mermaid)</h2>
<div class="mermaid-wrap">
<pre class="mermaid">{_h(data.mermaid_power)}</pre>
</div>

<h2>Clock Tree Diagram (Mermaid)</h2>
<div class="mermaid-wrap">
<pre class="mermaid">{_h(data.mermaid_clock)}</pre>
</div>

</section>

<footer>
  Generated by <strong>SoC-Consistency</strong> &nbsp;|&nbsp;
  <a href="https://github.com/woo/SoC-Consistency" style="color:var(--accent)">GitHub</a>
  &nbsp;|&nbsp; {_h(data.generated_at)}
</footer>

</body>
</html>
"""
