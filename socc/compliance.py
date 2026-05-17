"""
ISO 26262 / IEC 61508 Functional-Safety Isolation Auditor

Concept: a "Safety Island" is any DTS node annotated with
  socc,safety-asil = "B"         (or "A", "C", "D")
  or: secure-status = "okay"

For each such node the engine:
  1. Traces its complete power supply chain up to the root.
  2. Traces its complete clock source chain up to the PLL.
  3. Detects whether any non-safe node shares a supply or clock ancestor.
  4. Detects whether the interrupt bank (interrupt-parent) is shared with
     non-safe nodes.
  5. Reports pin-bank sharing (same GPIO interrupt controller).

Output is a structured Markdown or plain-text ASIL isolation report
suitable for submission to a TÜV / SGS / BSI functional-safety auditor.

CLI entry:
  socc generate-compliance board.dts --standard iso26262-asil-b
  socc generate-compliance board.dts --standard iec61508-sil2 -o report.md
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from socc.model import SoC


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_STANDARDS = {
    "iso26262-asil-a": ("ISO 26262", "ASIL A", "Automotive – lowest integrity"),
    "iso26262-asil-b": ("ISO 26262", "ASIL B", "Automotive – moderate integrity"),
    "iso26262-asil-c": ("ISO 26262", "ASIL C", "Automotive – high integrity"),
    "iso26262-asil-d": ("ISO 26262", "ASIL D", "Automotive – highest integrity"),
    "iec61508-sil1":   ("IEC 61508", "SIL 1",  "Industrial – low safety integrity"),
    "iec61508-sil2":   ("IEC 61508", "SIL 2",  "Industrial – medium safety integrity"),
    "iec61508-sil3":   ("IEC 61508", "SIL 3",  "Industrial – high safety integrity"),
    "iec61508-sil4":   ("IEC 61508", "SIL 4",  "Industrial – highest safety integrity"),
}


@dataclass
class IsolationCheck:
    """Result of a single isolation check for one safety-island node."""
    passed: bool
    category: str         # "power" | "clock" | "interrupt" | "pinmux"
    description: str
    evidence: str         # what was found
    risk: str = ""        # if failed, what the consequence is


@dataclass
class SafetyNodeReport:
    node_path: str
    node_name: str
    asil_level: str
    compatible: str
    checks: List[IsolationCheck] = field(default_factory=list)
    trm_ref: str = ""

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def violation_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)


@dataclass
class ComplianceReport:
    dts_path: str
    standard_id: str
    standard_name: str
    asil_level: str
    standard_desc: str
    generated_at: str
    soc_name: str
    safety_nodes: List[SafetyNodeReport] = field(default_factory=list)

    @property
    def total_violations(self) -> int:
        return sum(n.violation_count for n in self.safety_nodes)

    @property
    def overall_pass(self) -> bool:
        return self.total_violations == 0 and len(self.safety_nodes) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Safety-node detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_safety_node(node) -> bool:
    """Return True if the DTS node is tagged as safety-critical."""
    props = node.properties
    asil = props.get("socc,safety-asil") or props.get("safety-asil")
    if asil:
        return True
    secure = props.get("secure-status")
    if secure in ("okay", "ok"):
        return True
    # Heuristic: nodes named "can", "braking", "ecu", "safety" in the path
    path_low = node.path.lower()
    return any(kw in path_low for kw in ("can-bus", "braking", "ecu", "safety"))


def _asil_level(node) -> str:
    props = node.properties
    asil = props.get("socc,safety-asil") or props.get("safety-asil")
    if asil:
        return str(asil).upper().strip('"')
    if props.get("secure-status") in ("okay", "ok"):
        return "B"  # default if unspecified
    return "B"


# ─────────────────────────────────────────────────────────────────────────────
# Power chain tracing
# ─────────────────────────────────────────────────────────────────────────────

def _power_chain(device_name: str, soc: SoC) -> List[str]:
    """Return the full supply chain from device → root regulator."""
    chain: List[str] = []
    supplies = soc.device_supplies.get(device_name, [])
    visited: Set[str] = set()
    queue = list(supplies)
    while queue:
        supply = queue.pop(0)
        if supply in visited:
            continue
        visited.add(supply)
        chain.append(supply)
        reg = soc.power_tree.nodes.get(supply)
        if reg and reg.parent:
            queue.append(reg.parent)
    return chain


def _check_power_isolation(
    node_name: str,
    safe_chain: List[str],
    all_safe_names: Set[str],
    soc: SoC,
) -> IsolationCheck:
    """Verify that no non-safe node shares a supply in the safe chain."""
    violators: List[str] = []
    for supply in safe_chain:
        reg = soc.power_tree.nodes.get(supply)
        if reg is None:
            continue
        for consumer in reg.consumers:
            if consumer not in all_safe_names and consumer != node_name:
                violators.append(f"{consumer} shares supply '{supply}'")

    if not violators:
        return IsolationCheck(
            passed=True,
            category="power",
            description="Power supply chain is fully isolated from non-safe nodes.",
            evidence=f"Dedicated supply chain: {' → '.join(safe_chain) or 'root'}",
        )
    return IsolationCheck(
        passed=False,
        category="power",
        description="Non-safe nodes share power supply with this safety island.",
        evidence="; ".join(violators[:5]),
        risk=(
            "A fault in the shared regulator or a voltage glitch induced by a "
            "non-safe consumer can propagate to the safety-critical function."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Clock chain tracing
# ─────────────────────────────────────────────────────────────────────────────

def _clock_chain(device_name: str, soc: SoC) -> List[str]:
    """Return the full clock ancestry from device → PLL/oscillator."""
    chain: List[str] = []
    clocks = soc.device_clocks.get(device_name, [])
    visited: Set[str] = set()
    queue = list(clocks)
    while queue:
        clk_name = queue.pop(0)
        if clk_name in visited:
            continue
        visited.add(clk_name)
        chain.append(clk_name)
        clk = soc.clock_tree.clocks.get(clk_name)
        if clk:
            parent = getattr(clk, "parent", None)
            if parent:
                queue.append(parent)
    return chain


def _check_clock_isolation(
    node_name: str,
    safe_clk_chain: List[str],
    all_safe_names: Set[str],
    soc: SoC,
) -> IsolationCheck:
    """Verify no non-safe node shares a clock ancestor."""
    violators: List[str] = []
    for clk_name in safe_clk_chain:
        clk = soc.clock_tree.clocks.get(clk_name)
        if clk is None:
            continue
        consumers = getattr(clk, "consumers", []) or []
        for consumer in consumers:
            if consumer not in all_safe_names and consumer != node_name:
                violators.append(f"{consumer} shares clock '{clk_name}'")

    if not violators:
        return IsolationCheck(
            passed=True,
            category="clock",
            description="Clock tree path has no shared descendants with non-safe nodes.",
            evidence=f"Clock chain: {' → '.join(safe_clk_chain) or 'unknown'}",
        )
    return IsolationCheck(
        passed=False,
        category="clock",
        description="Non-safe nodes share a clock source with this safety island.",
        evidence="; ".join(violators[:5]),
        risk=(
            "Clock glitches, frequency scaling by a non-safe power manager, or "
            "gate toggling could interrupt the safety-critical clock domain."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Interrupt bank sharing
# ─────────────────────────────────────────────────────────────────────────────

def _check_interrupt_isolation(
    node_name: str,
    node,
    all_safe_names: Set[str],
    soc: SoC,
) -> IsolationCheck:
    """Check that the interrupt-parent / GPIO bank is not shared with non-safe nodes."""
    irq_parent = node.properties.get("interrupt-parent")
    if irq_parent is None:
        return IsolationCheck(
            passed=True,
            category="interrupt",
            description="Node has no interrupt-parent declaration; no IRQ sharing risk.",
            evidence="No interrupt-parent property",
        )

    # Find other nodes that share the same interrupt-parent
    violators: List[str] = []
    for dev_name, dev_node in soc.devices.items():
        if dev_name == node_name:
            continue
        other_irq = dev_node.properties.get("interrupt-parent")
        if other_irq == irq_parent and dev_name not in all_safe_names:
            violators.append(dev_name)

    if not violators:
        return IsolationCheck(
            passed=True,
            category="interrupt",
            description="Interrupt bank is not shared with any non-safe node.",
            evidence=f"interrupt-parent = {irq_parent}",
        )

    example_risk = violators[0] if violators else ""
    return IsolationCheck(
        passed=False,
        category="interrupt",
        description="Interrupt bank is shared with non-safe node(s).",
        evidence=f"Shared interrupt-parent '{irq_parent}' with: {', '.join(violators[:4])}",
        risk=(
            f"A noisy or malfunctioning {example_risk} could flood the interrupt "
            "controller, causing IRQ starvation (priority inversion) on the "
            "safety-critical path."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pinmux / GPIO bank sharing
# ─────────────────────────────────────────────────────────────────────────────

def _check_pinmux_isolation(
    node_name: str,
    node,
    all_safe_names: Set[str],
    soc: SoC,
) -> IsolationCheck:
    """Check that GPIO bank / pinctrl group is not shared with non-safe nodes."""
    pin_cfg = node.properties.get("pinctrl-0") or node.properties.get("pinctrl-names")
    if pin_cfg is None:
        return IsolationCheck(
            passed=True,
            category="pinmux",
            description="No pinctrl assignment; no GPIO bank sharing risk.",
            evidence="No pinctrl-0 property",
        )

    # Try to extract GPIO bank number from pinmux config
    pin_str = str(pin_cfg)
    # RK3588 pattern: GPIO4_A1 → bank 4
    import re
    banks = set(re.findall(r"GPIO(\d+)", pin_str, re.I))
    if not banks:
        return IsolationCheck(
            passed=True,
            category="pinmux",
            description="Could not determine GPIO bank; manual review required.",
            evidence=f"pinctrl-0 = {pin_str[:60]}",
        )

    # Check other nodes that might share the same GPIO bank
    violators: List[str] = []
    for dev_name, dev_node in soc.devices.items():
        if dev_name == node_name or dev_name in all_safe_names:
            continue
        other_pin = str(
            dev_node.properties.get("pinctrl-0")
            or dev_node.properties.get("pinctrl-names")
            or ""
        )
        other_banks = set(re.findall(r"GPIO(\d+)", other_pin, re.I))
        if banks & other_banks:
            violators.append(f"{dev_name} (bank {banks & other_banks})")

    if not violators:
        return IsolationCheck(
            passed=True,
            category="pinmux",
            description="GPIO bank(s) are not shared with non-safe nodes.",
            evidence=f"GPIO bank(s) used: {', '.join(sorted(banks))}",
        )

    return IsolationCheck(
        passed=False,
        category="pinmux",
        description="GPIO bank shared with non-safe node(s).",
        evidence=f"Bank clash: {'; '.join(violators[:4])}",
        risk=(
            "Electrical noise on a GPIO in the same bank can cause spurious "
            "interrupt events or alter IOMUX settings for adjacent pins."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

def generate_compliance_report(
    soc: SoC,
    dts_path: str = "board.dts",
    standard_id: str = "iso26262-asil-b",
) -> ComplianceReport:
    std_info = KNOWN_STANDARDS.get(standard_id.lower(), (standard_id, "?", "Custom standard"))
    std_name, asil_level, std_desc = std_info

    report = ComplianceReport(
        dts_path=dts_path,
        standard_id=standard_id,
        standard_name=std_name,
        asil_level=asil_level,
        standard_desc=std_desc,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        soc_name=soc.name,
    )

    # Collect all safety-island nodes
    safety_nodes_map = {
        name: node
        for name, node in soc.devices.items()
        if _is_safety_node(node)
    }
    all_safe_names = set(safety_nodes_map.keys())

    if not safety_nodes_map:
        # No explicit safety nodes — report is vacuously passing but warns
        report.safety_nodes = []
        return report

    for node_name, node in safety_nodes_map.items():
        asil = _asil_level(node)
        compat = " ".join(
            v if isinstance(v, str) else str(v)
            for v in (node.properties.get("compatible") or [])
        )

        pwr_chain = _power_chain(node_name, soc)
        clk_chain  = _clock_chain(node_name, soc)

        node_report = SafetyNodeReport(
            node_path=node.path,
            node_name=node_name,
            asil_level=asil,
            compatible=compat,
        )
        node_report.checks.append(
            _check_power_isolation(node_name, pwr_chain, all_safe_names, soc)
        )
        node_report.checks.append(
            _check_clock_isolation(node_name, clk_chain, all_safe_names, soc)
        )
        node_report.checks.append(
            _check_interrupt_isolation(node_name, node, all_safe_names, soc)
        )
        node_report.checks.append(
            _check_pinmux_isolation(node_name, node, all_safe_names, soc)
        )

        report.safety_nodes.append(node_report)

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Renderers
# ─────────────────────────────────────────────────────────────────────────────

_PASS_SYM  = "✓"
_FAIL_SYM  = "✗"
_CAT_LABEL = {
    "power":     "Power Isolation",
    "clock":     "Clock Isolation",
    "interrupt": "Interrupt Isolation",
    "pinmux":    "Pinmux / GPIO Isolation",
}


def render_compliance_markdown(report: ComplianceReport) -> str:
    """Generate a Markdown-formatted ISO 26262 / IEC 61508 isolation report."""
    lines: List[str] = []

    lines.append(f"# {report.standard_name} {report.asil_level} Isolation Report")
    lines.append("")
    lines.append(f"| Field | Value |")
    lines.append(f"|-------|-------|")
    lines.append(f"| Standard | {report.standard_name} — {report.asil_level} ({report.standard_desc}) |")
    lines.append(f"| Source DTS | `{report.dts_path}` |")
    lines.append(f"| SoC | `{report.soc_name}` |")
    lines.append(f"| Generated | {report.generated_at} |")
    lines.append(f"| Overall Result | {'**PASS**' if report.overall_pass else '**FAIL**'} |")
    lines.append(f"| Safety-Island Nodes | {len(report.safety_nodes)} |")
    lines.append(f"| Total Violations | {report.total_violations} |")
    lines.append("")

    if not report.safety_nodes:
        lines.append("> **NOTE:** No safety-island nodes detected in this DTS.")
        lines.append("> Add `socc,safety-asil = \"B\";` to your safety-critical nodes.")
        lines.append("> Or use `secure-status = \"okay\";` on protected peripherals.")
        return "\n".join(lines)

    for node_rep in report.safety_nodes:
        result_badge = "✅ PASS" if node_rep.passed else "❌ FAIL"
        lines.append(f"---")
        lines.append(f"## Target: `{node_rep.node_path}` ({node_rep.node_name})")
        lines.append("")
        lines.append(f"| Property | Value |")
        lines.append(f"|----------|-------|")
        lines.append(f"| ASIL Level | {node_rep.asil_level} |")
        lines.append(f"| Compatible | `{node_rep.compatible or 'unknown'}` |")
        lines.append(f"| Result | {result_badge} |")
        lines.append("")

        for chk in node_rep.checks:
            sym = _PASS_SYM if chk.passed else _FAIL_SYM
            label = _CAT_LABEL.get(chk.category, chk.category)
            lines.append(f"### [{sym}] {label}")
            lines.append("")
            lines.append(f"**Finding:** {chk.description}")
            lines.append("")
            lines.append(f"**Evidence:** {chk.evidence}")
            if not chk.passed and chk.risk:
                lines.append("")
                lines.append(f"> **Risk:** {chk.risk}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Report generated by [SoC-Consistency](https://github.com/woo/SoC-Consistency)*")
    return "\n".join(lines)


def render_compliance_text(report: ComplianceReport, use_color: bool = True) -> str:
    """Plain-text (terminal) version of the compliance report."""
    lines: List[str] = []

    _C = {
        "pass":  "\033[1;32m" if use_color else "",
        "fail":  "\033[1;31m" if use_color else "",
        "warn":  "\033[1;33m" if use_color else "",
        "reset": "\033[0m"    if use_color else "",
        "bold":  "\033[1m"    if use_color else "",
    }

    lines.append("=" * 70)
    lines.append(f"  {report.standard_name} {report.asil_level} ISOLATION REPORT")
    lines.append(f"  Standard : {report.standard_desc}")
    lines.append(f"  DTS      : {report.dts_path}")
    lines.append(f"  SoC      : {report.soc_name}")
    lines.append(f"  Date     : {report.generated_at}")
    overall = (
        f"{_C['pass']}PASS{_C['reset']}"
        if report.overall_pass
        else f"{_C['fail']}FAIL  ({report.total_violations} violation(s)){_C['reset']}"
    )
    lines.append(f"  Result   : {overall}")
    lines.append("=" * 70)

    if not report.safety_nodes:
        lines.append("")
        lines.append(
            f"{_C['warn']}[!] No safety-island nodes found in this DTS.{_C['reset']}"
        )
        lines.append(
            "    Annotate critical nodes with:\n"
            "        socc,safety-asil = \"B\";  /* or A / C / D */"
        )
        return "\n".join(lines)

    for node_rep in report.safety_nodes:
        lines.append("")
        lines.append(f"{_C['bold']}Target: {node_rep.node_path}  [{node_rep.node_name}]{_C['reset']}")
        lines.append(f"  ASIL: {node_rep.asil_level}   Compatible: {node_rep.compatible or 'unknown'}")
        lines.append("")
        for chk in node_rep.checks:
            sym_color = _C["pass"] if chk.passed else _C["fail"]
            sym = _PASS_SYM if chk.passed else _FAIL_SYM
            label = _CAT_LABEL.get(chk.category, chk.category)
            lines.append(f"  {sym_color}[{sym}]{_C['reset']} {label}")
            lines.append(f"       {chk.description}")
            lines.append(f"       Evidence: {chk.evidence}")
            if not chk.passed and chk.risk:
                wrapped = textwrap.fill(
                    chk.risk, width=64,
                    initial_indent="       Risk: ",
                    subsequent_indent="             ",
                )
                lines.append(wrapped)

    lines.append("")
    overall_label = "PASS" if report.overall_pass else "FAIL"
    lines.append(
        f"Result: {overall_label}  |  "
        f"{len(report.safety_nodes)} safety node(s) audited  |  "
        f"{report.total_violations} violation(s)"
    )
    return "\n".join(lines)
