"""
Memory Map Overlap & Out-of-Bounds Scanner
==========================================

Extracts every ``reg = <...>`` property from the parsed SoC device tree,
converts each entry to a half-open interval [base, base+size), then runs a
sweep-line algorithm to find overlapping, duplicate, and zero-size regions.

Also detects:
  - Regions larger than the physical address window (suspicious size)
  - Nodes that declare zero-size windows (likely copy-paste artefacts)
  - Nodes sharing the exact same base address (100 % overlap)

CLI entry:
  socc check-memory board.dts
  socc check-memory board.dts --min-severity warning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from socc.model import SoC, IRNode


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MemRegion:
    """A single contiguous MMIO window claimed by a DTS node."""
    node_name: str
    node_path: str
    base:  int          # inclusive start address
    size:  int          # byte length
    end:   int          # exclusive end  (base + size)
    index: int = 0      # which <reg> tuple this came from (multi-reg nodes)


@dataclass
class MemIssue:
    """A detected memory map problem."""
    severity: str        # "FATAL" | "ERROR" | "WARNING" | "INFO"
    rule_id:  str        # e.g. "MM-001"
    node_a:   MemRegion
    node_b:   Optional[MemRegion]  # None for single-node issues
    description: str
    suggestion:  str


@dataclass
class MemMapReport:
    """Full report from check_memory()."""
    regions:      List[MemRegion] = field(default_factory=list)
    issues:       List[MemIssue]  = field(default_factory=list)
    fatal_count:  int = 0
    error_count:  int = 0
    warning_count: int = 0

    # Quick helpers
    @property
    def total_issues(self) -> int:
        return len(self.issues)

    @property
    def pass_result(self) -> bool:
        return self.fatal_count == 0 and self.error_count == 0


# ── Address extraction ────────────────────────────────────────────────────────

# Suspicious single-region size threshold (> 4 GiB on a 32-bit bus is wrong)
_SUSPICIOUS_SIZE = 0x1_0000_0000   # 4 GiB

# Treat regions larger than this as "whole address space" (catch 0xffffffff typos)
_MAX_PLAUSIBLE_SIZE = 0x2000_0000  # 512 MiB — adjust per platform as needed


def _extract_regions(node: IRNode) -> List[Tuple[int, int]]:
    """
    Return [(base, size), ...] from a node's ``reg`` property.

    DTS reg encoding comes in several flavours:
      - Flat list of 32-bit cells:   [base, size, ...]
      - 64-bit addresses:            [base_hi, base_lo, size_hi, size_lo, ...]
      - Mixed #address-cells / #size-cells (guessed from list length)
    """
    raw = node.properties.get("reg")
    if raw is None:
        return []

    # Normalise to flat list of ints
    if isinstance(raw, int):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    items = [int(x) if not isinstance(x, int) else x for x in raw if x is not None]
    if not items:
        return []

    results: List[Tuple[int, int]] = []

    # Try to detect 64-bit address encoding (4 values per region)
    if len(items) % 4 == 0 and all(
        items[i] == 0 or items[i + 2] == 0 for i in range(0, len(items), 4)
    ):
        # Pattern: [addr_hi=0, addr_lo, size_hi=0, size_lo]
        for i in range(0, len(items), 4):
            base = (items[i] << 32) | items[i + 1]
            size = (items[i + 2] << 32) | items[i + 3]
            results.append((base, size))
    elif len(items) % 2 == 0:
        # Standard 32-bit: [base, size, base, size, ...]
        for i in range(0, len(items), 2):
            results.append((items[i], items[i + 1]))
    else:
        # Odd count: treat as single base address, size unknown
        results.append((items[0], 0))

    return results


def build_regions(soc: SoC) -> List[MemRegion]:
    """Walk all SoC devices and collect MemRegion objects."""
    regions: List[MemRegion] = []
    for dev_name, node in soc.devices.items():
        tuples = _extract_regions(node)
        for idx, (base, size) in enumerate(tuples):
            if base == 0 and size == 0:
                continue  # skip empty placeholder
            regions.append(MemRegion(
                node_name=dev_name,
                node_path=node.path,
                base=base,
                size=size,
                end=base + size,
                index=idx,
            ))
    return regions


# ── Sweep-line overlap detection ──────────────────────────────────────────────

def detect_overlaps(regions: List[MemRegion]) -> List[MemIssue]:
    """
    Run a sweep-line pass over sorted regions and return all overlap issues.

    O(n log n) time.
    """
    issues: List[MemIssue] = []

    # Sort by base address ascending
    sorted_r = sorted(regions, key=lambda r: (r.base, r.end))

    # Sweep: each region is checked against all previously-seen regions whose
    # end address has not yet passed our current base.
    active: List[MemRegion] = []

    for r in sorted_r:
        # Drop regions that ended before our base
        active = [a for a in active if a.end > r.base]

        for a in active:
            # Overlapping: a.base <= r.base < a.end  (since active regions
            # all start <= r.base and end > r.base)
            overlap_start = max(a.base, r.base)
            overlap_end   = min(a.end,  r.end)
            overlap_bytes = overlap_end - overlap_start

            if a.base == r.base and a.size == r.size:
                severity = "FATAL"
                rule_id  = "MM-001"
                desc = (
                    f"Identical MMIO window: both nodes claim "
                    f"[0x{a.base:08X} – 0x{a.end:08X}]"
                )
                sug = "One of these nodes is a duplicate or misconfigured alias."
            elif overlap_bytes == r.size:
                severity = "FATAL"
                rule_id  = "MM-002"
                desc = (
                    f"Full containment: {r.node_path} "
                    f"[0x{r.base:08X}+0x{r.size:X}] "
                    f"sits entirely inside {a.node_path} "
                    f"[0x{a.base:08X}+0x{a.size:X}]"
                )
                sug = "Verify reg sizes — likely a missing zero in size cell."
            else:
                severity = "ERROR"
                rule_id  = "MM-003"
                desc = (
                    f"Partial overlap of 0x{overlap_bytes:X} bytes: "
                    f"{a.node_path} [0x{a.base:08X}–0x{a.end:08X}] ∩ "
                    f"{r.node_path} [0x{r.base:08X}–0x{r.end:08X}]"
                )
                sug = (
                    "Check reg size cells. ioremap() of these two drivers will "
                    "produce silent data corruption."
                )

            issues.append(MemIssue(
                severity=severity,
                rule_id=rule_id,
                node_a=a,
                node_b=r,
                description=desc,
                suggestion=sug,
            ))

        active.append(r)

    return issues


def detect_single_node_problems(regions: List[MemRegion]) -> List[MemIssue]:
    """Detect zero-size and suspiciously large single-node issues."""
    issues: List[MemIssue] = []
    for r in regions:
        if r.size == 0:
            issues.append(MemIssue(
                severity="WARNING",
                rule_id="MM-004",
                node_a=r, node_b=None,
                description=(
                    f"Zero-size region at 0x{r.base:08X} in {r.node_path}"
                ),
                suggestion=(
                    "reg size cell is 0 — copy-paste error or placeholder? "
                    "The driver will not be able to ioremap this device."
                ),
            ))
        elif r.size > _MAX_PLAUSIBLE_SIZE:
            issues.append(MemIssue(
                severity="WARNING",
                rule_id="MM-005",
                node_a=r, node_b=None,
                description=(
                    f"Suspiciously large region: {r.node_path} "
                    f"claims 0x{r.size:X} bytes "
                    f"(>= {_MAX_PLAUSIBLE_SIZE // 0x100_000} MiB)"
                ),
                suggestion=(
                    "Verify the size cell — a missing trailing zero turns "
                    "0x10000 (64 KiB) into 0x100000 (1 MiB), and so on."
                ),
            ))
    return issues


# ── Main entry point ──────────────────────────────────────────────────────────

def check_memory(soc: SoC) -> MemMapReport:
    """
    Full memory map audit: build regions, run overlap + sanity checks,
    return a MemMapReport.
    """
    report = MemMapReport()
    report.regions = build_regions(soc)

    report.issues  = (
        detect_overlaps(report.regions)
        + detect_single_node_problems(report.regions)
    )

    # Sort: FATAL → ERROR → WARNING
    _order = {"FATAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3}
    report.issues.sort(key=lambda i: _order.get(i.severity, 9))

    report.fatal_count   = sum(1 for i in report.issues if i.severity == "FATAL")
    report.error_count   = sum(1 for i in report.issues if i.severity == "ERROR")
    report.warning_count = sum(1 for i in report.issues if i.severity == "WARNING")
    return report


# ── Renderer ──────────────────────────────────────────────────────────────────

_COLOR = {
    "FATAL":   "\033[1;35m",  # bold magenta
    "ERROR":   "\033[1;31m",  # bold red
    "WARNING": "\033[1;33m",  # bold yellow
    "INFO":    "\033[1;36m",  # bold cyan
    "RESET":   "\033[0m",
    "BOLD":    "\033[1m",
    "DIM":     "\033[2m",
    "GREEN":   "\033[1;32m",
}


def render_memmap_report(report: MemMapReport, use_color: bool = True) -> str:
    C = _COLOR if use_color else {k: "" for k in _COLOR}
    lines: List[str] = []

    banner = "SOCC MEMORY MAP OVERLAP SCANNER"
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"{C['BOLD']}{banner}{C['RESET']}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(
        f"  Regions scanned : {C['BOLD']}{len(report.regions)}{C['RESET']}"
    )
    lines.append(
        f"  Issues found    : "
        f"{C['FATAL']}{report.fatal_count} FATAL{C['RESET']}  "
        f"{C['ERROR']}{report.error_count} ERROR{C['RESET']}  "
        f"{C['WARNING']}{report.warning_count} WARNING{C['RESET']}"
    )
    lines.append("")

    if not report.issues:
        lines.append(f"{C['GREEN']}[✓] No memory map conflicts detected.{C['RESET']}")
    else:
        for issue in report.issues:
            sc = C.get(issue.severity, "")
            lines.append(
                f"{sc}[{issue.severity}] {issue.rule_id}{C['RESET']} "
                f"{issue.description}"
            )
            if issue.node_b:
                lines.append(
                    f"  {C['DIM']}Node A: {issue.node_a.node_path}"
                    f"  [0x{issue.node_a.base:08X} – 0x{issue.node_a.end - 1:08X}]"
                    f"  size=0x{issue.node_a.size:X}{C['RESET']}"
                )
                lines.append(
                    f"  {C['DIM']}Node B: {issue.node_b.node_path}"
                    f"  [0x{issue.node_b.base:08X} – 0x{issue.node_b.end - 1:08X}]"
                    f"  size=0x{issue.node_b.size:X}{C['RESET']}"
                )
            else:
                lines.append(
                    f"  {C['DIM']}{issue.node_a.node_path}"
                    f"  base=0x{issue.node_a.base:08X}"
                    f"  size=0x{issue.node_a.size:X}{C['RESET']}"
                )
            lines.append(f"  {C['DIM']}→ {issue.suggestion}{C['RESET']}")
            lines.append("")

    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    status = (
        f"{C['GREEN']}PASS{C['RESET']}" if report.pass_result
        else f"{C['FATAL']}FAIL{C['RESET']}"
    )
    lines.append(f"  Result: {status}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    return "\n".join(lines)
