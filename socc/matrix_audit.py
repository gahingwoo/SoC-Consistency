"""
Multi-SKU Variant Matrix Audit — Supply-Chain Risk Detector

Reads every .dts file in a board directory, parses each into a SoC model,
and builds a cross-product impact matrix.  When a shared base parameter
(clock frequency, supply voltage, pinmux function) differs between boards
or when a single-board change is projected across all relatives, the engine
flags the physical propagation chain with severity.

CLI entry:
  socc matrix-audit boards/
  socc matrix-audit boards/ --change-file change.yaml
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from socc.model import SoC
from socc.parser import parse_dts_file


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BoardProfile:
    """Distilled view of one DTS board file."""
    name: str                       # stem of the .dts file
    dts_path: str
    soc_name: str
    clocks: Dict[str, float]        # clock-name → Hz
    voltages: Dict[str, float]      # supply-name → V
    devices: Set[str]               # set of device names (with status okay)
    includes: List[str]             # .dtsi files this board includes


@dataclass
class MatrixIssue:
    severity: str                   # "FATAL" | "CRITICAL" | "WARNING" | "INFO"
    board_name: str
    category: str                   # "clock" | "voltage" | "pinmux" | "device"
    description: str
    detail: str
    suggestion: str = ""


@dataclass
class VariantMatrix:
    boards_dir: str
    profiles: List[BoardProfile] = field(default_factory=list)
    issues: List[MatrixIssue] = field(default_factory=list)
    change_summary: str = ""        # human description of the change being evaluated

    @property
    def fatal_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "FATAL")

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")

    @property
    def is_safe(self) -> bool:
        return self.fatal_count == 0 and self.critical_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Board scanning
# ─────────────────────────────────────────────────────────────────────────────

def _detect_includes(dts_path: str) -> List[str]:
    """Cheaply extract #include / /include/ lines without full parsing."""
    includes: List[str] = []
    try:
        text = Path(dts_path).read_text(errors="replace")
        for m in re.finditer(r'(?:#include|/include/)\s+"([^"]+\.dtsi)"', text):
            includes.append(m.group(1))
    except OSError:
        pass
    return includes


def _soc_clocks(soc: SoC) -> Dict[str, float]:
    """Extract clock-name → Hz from the SoC clock tree."""
    result: Dict[str, float] = {}
    for clk_name, clk in soc.clock_tree.clocks.items():
        hz = getattr(clk, "rate", None) or getattr(clk, "frequency", 0) or 0
        if hz:
            result[clk_name] = float(hz)
    return result


def _soc_voltages(soc: SoC) -> Dict[str, float]:
    """Extract supply-name → V from the power tree."""
    result: Dict[str, float] = {}
    for name, reg in soc.power_tree.nodes.items():
        v = getattr(reg, "voltage_max", 0) or 0
        if v:
            result[name] = float(v)
    return result


def _soc_devices(soc: SoC) -> Set[str]:
    enabled: Set[str] = set()
    for dev_name, node in soc.devices.items():
        status = node.properties.get("status", "okay")
        if status in ("okay", "ok"):
            enabled.add(dev_name)
    return enabled


def _profile_from_soc(soc: SoC, dts_path: str) -> BoardProfile:
    includes = _detect_includes(dts_path)
    return BoardProfile(
        name=Path(dts_path).stem,
        dts_path=dts_path,
        soc_name=soc.name,
        clocks=_soc_clocks(soc),
        voltages=_soc_voltages(soc),
        devices=_soc_devices(soc),
        includes=includes,
    )


def scan_boards_dir(boards_dir: str) -> List[BoardProfile]:
    """Parse every .dts file found recursively under boards_dir."""
    profiles: List[BoardProfile] = []
    for p in sorted(Path(boards_dir).rglob("*.dts")):
        try:
            soc = parse_dts_file(str(p))
            profiles.append(_profile_from_soc(soc, str(p)))
        except Exception:
            # Non-fatal: skip unparseable files
            continue
    return profiles


# ─────────────────────────────────────────────────────────────────────────────
# Analysis passes
# ─────────────────────────────────────────────────────────────────────────────

# Voltage safety thresholds (V)
_ABSOLUTE_MAX: Dict[str, float] = {
    "1v8": 2.0,
    "1v2": 1.35,
    "0v9": 1.05,
    "3v3": 3.6,
}

# Maximum safe clock frequencies for common domains (Hz)
_CLOCK_MAX: Dict[str, float] = {
    "pll_audio": 1_200_000_000,
    "pll_npll":  1_800_000_000,
    "pll_apll":  2_400_000_000,
    "pll_gpll":  1_188_000_000,
    "pll_cpll":  1_500_000_000,
}


def _check_clock_cross_sku(profiles: List[BoardProfile]) -> List[MatrixIssue]:
    """Detect clock frequencies that differ across SKUs and flag unsafe ones."""
    issues: List[MatrixIssue] = []

    # Collect all clock names and their values per board
    all_clocks: Dict[str, Dict[str, float]] = {}  # clock → {board: Hz}
    for bp in profiles:
        for clk, hz in bp.clocks.items():
            all_clocks.setdefault(clk, {})[bp.name] = hz

    for clk, board_vals in all_clocks.items():
        vals = list(board_vals.values())
        if len(set(vals)) <= 1:
            continue  # uniform — no risk

        max_hz = max(vals)
        for board_name, hz in board_vals.items():
            if hz == max_hz:
                continue  # this is the high-frequency board, it should be checked
            # Other boards have lower clocks — check if the max exceeds their safe limit
            # Find matching threshold key
            clk_low = clk.lower().replace("-", "_")
            limit = None
            for k, lim in _CLOCK_MAX.items():
                if k in clk_low:
                    limit = lim
                    break
            if limit and max_hz > limit:
                issues.append(MatrixIssue(
                    severity="FATAL",
                    board_name=board_name,
                    category="clock",
                    description=(
                        f"Clock '{clk}' set to {max_hz/1e6:.0f} MHz in a sibling SKU "
                        f"but this board uses {hz/1e6:.0f} MHz"
                    ),
                    detail=(
                        f"If the base .dtsi is updated to {max_hz/1e6:.0f} MHz, "
                        f"this board's hardware may not support it (limit: {limit/1e6:.0f} MHz)."
                    ),
                    suggestion=(
                        f"Override '{clk}' in {board_name}.dts, or ensure all SKUs "
                        f"use a PMIC/oscillator capable of {max_hz/1e6:.0f} MHz."
                    ),
                ))

    return issues


def _check_voltage_cross_sku(profiles: List[BoardProfile]) -> List[MatrixIssue]:
    """Flag supply voltage differences that could brick lower-spec boards."""
    issues: List[MatrixIssue] = []

    all_supplies: Dict[str, Dict[str, float]] = {}
    for bp in profiles:
        for supply, v in bp.voltages.items():
            all_supplies.setdefault(supply, {})[bp.name] = v

    for supply, board_vals in all_supplies.items():
        vals = list(board_vals.values())
        if len(set(vals)) <= 1:
            continue

        max_v = max(vals)
        min_v = min(vals)
        if max_v - min_v < 0.05:
            continue  # < 50 mV difference is noise

        supply_low = supply.lower()
        # Detect domain
        for domain_key, abs_max in _ABSOLUTE_MAX.items():
            if domain_key in supply_low:
                if max_v > abs_max:
                    boards_at_min = [b for b, v in board_vals.items() if v == min_v]
                    issues.append(MatrixIssue(
                        severity="FATAL",
                        board_name=", ".join(boards_at_min),
                        category="voltage",
                        description=(
                            f"Supply '{supply}' reaches {max_v:.2f}V on some SKUs; "
                            f"boards with {domain_key.replace('v', 'V')} silicon "
                            f"have abs-max {abs_max:.2f}V"
                        ),
                        detail=(
                            f"{min_v:.2f}V-rated boards would be destroyed if base "
                            f"supply is bumped to {max_v:.2f}V."
                        ),
                        suggestion=(
                            f"Add per-board override in the relevant .dts files, "
                            f"or source a higher-rated regulator for all SKUs."
                        ),
                    ))
                break

    return issues


def _check_device_divergence(profiles: List[BoardProfile]) -> List[MatrixIssue]:
    """Warn when a device is present on some SKUs but absent on others."""
    issues: List[MatrixIssue] = []
    if len(profiles) < 2:
        return issues

    all_devices: Set[str] = set()
    for bp in profiles:
        all_devices |= bp.devices

    for dev in sorted(all_devices):
        present = [bp.name for bp in profiles if dev in bp.devices]
        absent  = [bp.name for bp in profiles if dev not in bp.devices]
        if not present or not absent:
            continue
        # Only report if > half the boards miss it and dev looks significant
        if len(absent) < len(profiles) // 2:
            continue
        issues.append(MatrixIssue(
            severity="WARNING",
            board_name=", ".join(absent),
            category="device",
            description=f"Device '{dev}' enabled on [{', '.join(present)}] but absent on this board",
            detail=(
                "A base .dtsi driver update or pinctrl change for this device "
                "may silently break boards that don't have the hardware."
            ),
            suggestion=(
                f"Ensure '{dev}' node has status='disabled' in {', '.join(absent)} "
                "if the hardware is not populated."
            ),
        ))

    return issues


def _check_shared_base(profiles: List[BoardProfile]) -> List[MatrixIssue]:
    """Identify boards that share .dtsi includes and flag cross-contamination risk."""
    issues: List[MatrixIssue] = []

    # Find shared base files
    include_map: Dict[str, List[str]] = {}  # dtsi → [board, ...]
    for bp in profiles:
        for inc in bp.includes:
            stem = Path(inc).name
            include_map.setdefault(stem, []).append(bp.name)

    for dtsi, boards in include_map.items():
        if len(boards) >= 2:
            issues.append(MatrixIssue(
                severity="INFO",
                board_name=", ".join(boards),
                category="shared-base",
                description=f"All these SKUs include '{dtsi}'",
                detail=(
                    f"Any change to '{dtsi}' will affect {len(boards)} boards simultaneously. "
                    "Run matrix-audit after every base file edit."
                ),
                suggestion="Tag base-file changes with a matrix-audit gate in CI.",
            ))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Simulated change propagation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PropagatedChange:
    """A hypothetical change being audited for cross-SKU impact."""
    entity_type: str   # "clock" | "supply" | "device"
    entity_name: str
    old_value: str
    new_value: str


def simulate_change_impact(
    profiles: List[BoardProfile],
    change: PropagatedChange,
) -> List[MatrixIssue]:
    """Propagate a single proposed change across all known SKUs."""
    issues: List[MatrixIssue] = []

    try:
        new_hz = float(change.new_value.rstrip("MmGgHhzZ").strip()) * (
            1e6 if "M" in change.new_value.upper() else
            1e9 if "G" in change.new_value.upper() else 1
        )
    except ValueError:
        new_hz = None

    try:
        new_v = float(change.new_value.rstrip("VvMm").strip()) / (
            1000 if change.new_value.upper().endswith("MV") else 1
        )
    except ValueError:
        new_v = None

    for bp in profiles:
        if change.entity_type == "clock":
            current_hz = bp.clocks.get(change.entity_name, 0.0)
            if new_hz and new_hz > current_hz:
                # Check against limits
                clk_low = change.entity_name.lower()
                limit = next(
                    (lim for k, lim in _CLOCK_MAX.items() if k in clk_low), None
                )
                if limit and new_hz > limit:
                    issues.append(MatrixIssue(
                        severity="FATAL",
                        board_name=bp.name,
                        category="clock",
                        description=(
                            f"Proposed change: '{change.entity_name}' "
                            f"{change.old_value} → {change.new_value}"
                        ),
                        detail=(
                            f"Board '{bp.name}' currently uses {current_hz/1e6:.0f} MHz. "
                            f"New value {new_hz/1e6:.0f} MHz exceeds safe limit "
                            f"{limit/1e6:.0f} MHz."
                        ),
                        suggestion=(
                            f"Override '{change.entity_name}' in {bp.name}.dts "
                            "or use a different PMIC/oscillator on this SKU."
                        ),
                    ))
                else:
                    issues.append(MatrixIssue(
                        severity="INFO" if (limit is None or new_hz <= limit) else "WARNING",
                        board_name=bp.name,
                        category="clock",
                        description=(
                            f"'{change.entity_name}' will increase on this board: "
                            f"{current_hz/1e6:.0f} → {new_hz/1e6:.0f} MHz"
                        ),
                        detail="Verify the oscillator and PLL chain can sustain the new frequency.",
                        suggestion="Run thermal characterisation after update.",
                    ))

        elif change.entity_type == "supply":
            current_v = bp.voltages.get(change.entity_name, 0.0)
            if new_v and abs(new_v - current_v) > 0.05:
                supply_low = change.entity_name.lower()
                for domain_key, abs_max in _ABSOLUTE_MAX.items():
                    if domain_key in supply_low:
                        sev = "FATAL" if new_v > abs_max else "WARNING"
                        issues.append(MatrixIssue(
                            severity=sev,
                            board_name=bp.name,
                            category="voltage",
                            description=(
                                f"'{change.entity_name}' {change.old_value} → {change.new_value}"
                            ),
                            detail=(
                                f"Board '{bp.name}' will see {new_v:.3f}V "
                                f"(abs-max for {domain_key} domain: {abs_max:.2f}V)."
                            ),
                            suggestion=(
                                "Verify PMIC output capability and IC absolute-maximum ratings."
                            ),
                        ))
                        break

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_matrix_audit(
    boards_dir: str,
    proposed_change: Optional[PropagatedChange] = None,
) -> VariantMatrix:
    profiles = scan_boards_dir(boards_dir)

    matrix = VariantMatrix(boards_dir=boards_dir, profiles=profiles)

    if proposed_change:
        matrix.change_summary = (
            f"Proposed change: [{proposed_change.entity_type}] "
            f"'{proposed_change.entity_name}' "
            f"{proposed_change.old_value} → {proposed_change.new_value}"
        )
        matrix.issues.extend(simulate_change_impact(profiles, proposed_change))
    else:
        matrix.issues.extend(_check_clock_cross_sku(profiles))
        matrix.issues.extend(_check_voltage_cross_sku(profiles))
        matrix.issues.extend(_check_device_divergence(profiles))
        matrix.issues.extend(_check_shared_base(profiles))

    # Sort: FATAL first
    _order = {"FATAL": 0, "CRITICAL": 1, "WARNING": 2, "INFO": 3}
    matrix.issues.sort(key=lambda i: _order.get(i.severity, 9))

    return matrix


# ─────────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────────

_COLORS = {
    "FATAL":    "\033[1;31m",
    "CRITICAL": "\033[1;35m",
    "WARNING":  "\033[1;33m",
    "INFO":     "\033[1;36m",
    "RESET":    "\033[0m",
}


def render_matrix_report(matrix: VariantMatrix, use_color: bool = True) -> str:
    lines: List[str] = []

    def c(sev: str, text: str) -> str:
        if not use_color:
            return text
        return f"{_COLORS.get(sev, '')}{text}{_COLORS['RESET']}"

    lines.append("=" * 70)
    lines.append("  SOCC SKU MATRIX AUDIT")
    lines.append(f"  Directory : {matrix.boards_dir}")
    lines.append(f"  Boards    : {len(matrix.profiles)} DTS file(s) parsed")
    if matrix.change_summary:
        lines.append(f"  Change    : {matrix.change_summary}")
    lines.append("=" * 70)

    # Board summary table
    lines.append("")
    lines.append("  BOARD PROFILES:")
    for bp in matrix.profiles:
        n_clocks = len(bp.clocks)
        n_supplies = len(bp.voltages)
        n_devices = len(bp.devices)
        lines.append(
            f"  ▸ {bp.name:<30s}  "
            f"{n_clocks} clocks  {n_supplies} supplies  {n_devices} devices"
        )

    lines.append("")
    if not matrix.issues:
        lines.append(c("INFO", "  [✓] No cross-SKU issues detected."))
    else:
        lines.append("  ISSUES:")
        for issue in matrix.issues:
            lines.append("")
            lines.append(c(issue.severity, f"  [{issue.severity}] [{issue.category.upper()}]  {issue.board_name}"))
            lines.append(f"  Description : {issue.description}")
            lines.append(f"  Detail      : {issue.detail}")
            if issue.suggestion:
                lines.append(f"  Action      : {issue.suggestion}")

    lines.append("")
    lines.append(
        f"Summary: {matrix.fatal_count} fatal, "
        f"{matrix.critical_count} critical, "
        f"{sum(1 for i in matrix.issues if i.severity=='WARNING')} warning(s)."
    )
    return "\n".join(lines)
