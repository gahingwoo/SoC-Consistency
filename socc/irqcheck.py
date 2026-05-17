"""
IRQ Collision & Routing Checker
=================================

Detects two classes of interrupt-related bugs that cause silent kernel
death or 100% CPU interrupt storms:

  1. **IRQ Collision (IRQ-C01)**
     Two or more *active* (status = okay) nodes claim the same GIC
     hardware interrupt number (SPI/PPI) without declaring the interrupt
     as shared (``IRQF_SHARED``).  The Linux kernel will hand the first
     device the line and refuse the second with ``-EBUSY``, OR worse,
     service the wrong ISR when the interrupt fires.

  2. **IRQ Storm Candidate (IRQ-C02)**
     A node claims a PPI interrupt line (0–15 in GIC notation, type = 1)
     that is architecturally reserved for SGI, FIQ, or timer use.
     Any driver binding to these lines will loop forever.

  3. **Interrupt Parent Mismatch (IRQ-C03)**
     A node's ``interrupt-parent`` phandle points to a node that either
     does not exist in the device tree or is disabled.  Mis-routing causes
     the interrupt to fire on the wrong CPU cluster / power domain.

  4. **No Interrupt Cells Spec (IRQ-C04)**
     An interrupt controller is referenced (``interrupt-parent``) but
     declares no ``#interrupt-cells`` property, making the cell layout
     of any child ``interrupts`` property undefined.

GIC interrupt encoding (standard ARM GIC-400 / GIC-500):
  ``interrupts = <type  number  flags>``
  type  0 = SPI (Shared Peripheral Interrupt, base 32)
  type  1 = PPI (Private Peripheral Interrupt, base 16)
  number  = hardware interrupt number within type
  flags   = trigger type (bit field)

CLI:
  socc check-irq board.dts
  socc check-irq board.dts --soc rk3588
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from socc.model import SoC, IRNode


# ── Data classes ──────────────────────────────────────────────────────────────

GIC_SPI = 0   # Shared Peripheral Interrupt
GIC_PPI = 1   # Private Peripheral Interrupt

# PPI lines 0-15 that are architecturally reserved (FIQ, SGI, timer).
# Binding a device driver ISR here will loop the core.
_RESERVED_PPI: Set[int] = {0, 1, 2, 3, 4, 5, 6, 7,  # SGI 0-7
                            13, 14, 15}               # Non-Secure/Sec/VTimer


@dataclass
class IRQClaim:
    node_name: str
    node_path: str
    irq_type:  int     # GIC_SPI or GIC_PPI
    irq_num:   int
    flags:     int
    global_irq: int    # SPI → 32+num, PPI → 16+num


@dataclass
class IRQIssue:
    severity:    str   # "CRITICAL" | "ERROR" | "WARNING"
    rule_id:     str   # "IRQ-C01" …
    description: str
    suggestion:  str
    nodes:       List[Tuple[str, str]]   # [(name, path), ...]
    irq_key:     str   # human-readable "GIC SPI 45"


@dataclass
class IRQReport:
    issues:       List[IRQIssue] = field(default_factory=list)
    total_claims: int = 0
    unique_lines: int = 0

    @property
    def pass_result(self) -> bool:
        return not any(i.severity == "CRITICAL" for i in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")


# ── Interrupt property parser ─────────────────────────────────────────────────

def _parse_interrupts(prop_val: Any) -> List[IRQClaim]:
    """
    Parse ``interrupts`` or ``interrupts-extended`` values into IRQClaim list.

    Supports the common 3-cell GIC encoding: [type, number, flags].
    Also handles plain 1-cell encoding (IRQ line only).
    """
    if not isinstance(prop_val, list):
        return []

    claims: List[IRQClaim] = []
    items = prop_val

    # Try 3-cell GIC encoding
    if len(items) >= 3 and all(isinstance(x, int) for x in items[:3]):
        # May have multiple triplets
        i = 0
        while i + 2 < len(items):
            try:
                irq_type  = int(items[i])
                irq_num   = int(items[i + 1])
                flags     = int(items[i + 2])
            except (TypeError, ValueError):
                break
            global_irq = (32 + irq_num) if irq_type == GIC_SPI else (16 + irq_num)
            claims.append(IRQClaim(
                node_name="",  # filled by caller
                node_path="",
                irq_type=irq_type,
                irq_num=irq_num,
                flags=flags,
                global_irq=global_irq,
            ))
            i += 3
    elif len(items) >= 1 and isinstance(items[0], int):
        # 1-cell encoding — treat as raw SPI number
        try:
            irq_num = int(items[0])
        except (TypeError, ValueError):
            return claims
        claims.append(IRQClaim(
            node_name="", node_path="",
            irq_type=GIC_SPI,
            irq_num=irq_num,
            flags=0,
            global_irq=32 + irq_num,
        ))

    return claims


# ── Interrupt controller finder ───────────────────────────────────────────────

def _find_interrupt_controllers(devices: Dict[str, IRNode]) -> Set[str]:
    """Return names of nodes that are interrupt controllers."""
    ctrls: Set[str] = set()
    for name, node in devices.items():
        if (node.properties.get("interrupt-controller") is not None
                or "#interrupt-cells" in node.properties):
            ctrls.add(name)
    return ctrls


# ── Main analysis ─────────────────────────────────────────────────────────────

def check_irq(soc: SoC) -> IRQReport:
    """Run IRQ collision & routing analysis on a SoC model."""
    devices  = soc.devices
    report   = IRQReport()

    int_ctrls = _find_interrupt_controllers(devices)

    # ── Stage 1: collect all claims from active nodes ────────────────────────
    # key = (irq_type, irq_num)  →  list of IRQClaim
    claim_map: Dict[Tuple[int, int], List[IRQClaim]] = defaultdict(list)

    for name, node in devices.items():
        status = node.properties.get("status", "okay")
        if str(status).lower() not in ("okay", "ok"):
            continue

        irq_val = node.properties.get("interrupts")
        if irq_val is None:
            continue

        claims = _parse_interrupts(irq_val)
        for c in claims:
            c.node_name = name
            c.node_path = node.path
            claim_map[(c.irq_type, c.irq_num)].append(c)

    report.total_claims = sum(len(v) for v in claim_map.values())
    report.unique_lines = len(claim_map)

    # ── Stage 2: collision detection (IRQ-C01) ───────────────────────────────
    for (irq_type, irq_num), claims in sorted(claim_map.items()):
        if len(claims) < 2:
            continue
        type_str = "SPI" if irq_type == GIC_SPI else "PPI"
        key = f"GIC {type_str} IRQ {irq_num}"
        report.issues.append(IRQIssue(
            severity="CRITICAL",
            rule_id="IRQ-C01",
            description=(
                f"Non-shared interrupt {key} claimed by "
                f"{len(claims)} active nodes simultaneously."
            ),
            suggestion=(
                "Assign unique interrupt lines to each peripheral, or "
                "enable IRQF_SHARED in both drivers if intentional."
            ),
            nodes=[(c.node_name, c.node_path) for c in claims],
            irq_key=key,
        ))

    # ── Stage 3: reserved PPI lines (IRQ-C02) ────────────────────────────────
    for (irq_type, irq_num), claims in claim_map.items():
        if irq_type != GIC_PPI:
            continue
        if irq_num not in _RESERVED_PPI:
            continue
        for c in claims:
            type_str = "PPI"
            key = f"GIC PPI IRQ {irq_num}"
            report.issues.append(IRQIssue(
                severity="ERROR",
                rule_id="IRQ-C02",
                description=(
                    f"Node '{c.node_name}' claims PPI line {irq_num} "
                    f"which is architecturally reserved (SGI/timer)."
                ),
                suggestion=(
                    "PPI lines 0-7 are SGI-reserved and 13-15 are "
                    "timer/FIQ lines. Do not bind device drivers here."
                ),
                nodes=[(c.node_name, c.node_path)],
                irq_key=key,
            ))

    # ── Stage 4: interrupt-parent mismatch (IRQ-C03) ─────────────────────────
    for name, node in devices.items():
        ip = node.properties.get("interrupt-parent")
        if ip is None:
            continue
        if isinstance(ip, str):
            ip_ref = ip.lstrip("&")
        else:
            continue  # numeric phandle — can't validate without phandle table

        if ip_ref not in devices:
            report.issues.append(IRQIssue(
                severity="ERROR",
                rule_id="IRQ-C03",
                description=(
                    f"Node '{name}' references interrupt-parent '&{ip_ref}' "
                    f"which does not exist in the device tree."
                ),
                suggestion=(
                    f"Ensure '{ip_ref}' is defined and enabled, or "
                    f"remove the explicit interrupt-parent to inherit root GIC."
                ),
                nodes=[(name, node.path)],
                irq_key=f"interrupt-parent=&{ip_ref}",
            ))
        else:
            parent_node = devices[ip_ref]
            parent_status = parent_node.properties.get("status", "okay")
            if str(parent_status).lower() not in ("okay", "ok"):
                report.issues.append(IRQIssue(
                    severity="ERROR",
                    rule_id="IRQ-C03",
                    description=(
                        f"Node '{name}' routes interrupts to disabled "
                        f"interrupt-parent '&{ip_ref}'."
                    ),
                    suggestion=(
                        f"Enable '{ip_ref}' (set status = \"okay\") or "
                        f"update interrupt-parent to an active controller."
                    ),
                    nodes=[(name, node.path), (ip_ref, parent_node.path)],
                    irq_key=f"interrupt-parent=&{ip_ref}",
                ))

    # ── Stage 5: missing #interrupt-cells on referenced controllers (IRQ-C04) ─
    referenced_ctrls: Set[str] = set()
    for name, node in devices.items():
        ip = node.properties.get("interrupt-parent")
        if isinstance(ip, str) and ip.startswith("&"):
            referenced_ctrls.add(ip[1:])

    for ctrl_name in referenced_ctrls:
        if ctrl_name not in devices:
            continue
        ctrl_node = devices[ctrl_name]
        if "#interrupt-cells" not in ctrl_node.properties:
            report.issues.append(IRQIssue(
                severity="WARNING",
                rule_id="IRQ-C04",
                description=(
                    f"Interrupt controller '&{ctrl_name}' is referenced via "
                    f"interrupt-parent but declares no '#interrupt-cells'."
                ),
                suggestion=(
                    f"Add '#interrupt-cells = <{3}>;' to the "
                    f"'{ctrl_name}' node (3 for standard GIC encoding)."
                ),
                nodes=[(ctrl_name, ctrl_node.path)],
                irq_key=f"#{ctrl_name} interrupt-cells",
            ))

    return report


# ── Renderer ──────────────────────────────────────────────────────────────────

_COLOR = {
    "CRITICAL": "\033[1;31m", "ERROR": "\033[1;33m",
    "WARNING":  "\033[33m",   "OK":    "\033[1;32m",
    "BOLD":     "\033[1m",    "RESET": "\033[0m",
    "DIM":      "\033[2m",    "CYAN":  "\033[1;36m",
}


def render_irq_text(report: IRQReport, use_color: bool = True) -> str:
    C = _COLOR if use_color else {k: "" for k in _COLOR}
    lines: List[str] = []
    banner = "SOCC IRQ COLLISION & ROUTING CHECKER"
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"{C['BOLD']}{banner}{C['RESET']}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"  Active IRQ claims : {report.total_claims}")
    lines.append(f"  Unique IRQ lines  : {report.unique_lines}")
    lines.append(
        f"  Issues found     : "
        f"{C['CRITICAL'] if report.critical_count else C['OK']}"
        f"{len(report.issues)}{C['RESET']}"
    )
    lines.append("")

    if not report.issues:
        lines.append(f"{C['OK']}[✓] No IRQ collisions or routing errors found.{C['RESET']}")
    else:
        for issue in report.issues:
            sc = C.get(issue.severity, C["BOLD"])
            lines.append(f"{sc}[{issue.severity}] {issue.rule_id}{C['RESET']}  "
                         f"{C['DIM']}{issue.irq_key}{C['RESET']}")
            lines.append(f"    🔥 {issue.description}")
            for nname, npath in issue.nodes:
                lines.append(f"    {C['DIM']}• {nname} → {npath}{C['RESET']}")
            lines.append(f"    {C['DIM']}💡 {issue.suggestion}{C['RESET']}")
            lines.append("")

    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    return "\n".join(lines)


def render_irq_json(report: IRQReport) -> str:
    return json.dumps({
        "pass_result":    report.pass_result,
        "total_claims":   report.total_claims,
        "unique_lines":   report.unique_lines,
        "critical_count": report.critical_count,
        "issues": [
            {
                "severity":    i.severity,
                "rule_id":     i.rule_id,
                "irq_key":     i.irq_key,
                "description": i.description,
                "suggestion":  i.suggestion,
                "nodes":       [{"name": n, "path": p} for n, p in i.nodes],
            }
            for i in report.issues
        ],
    }, indent=2)
