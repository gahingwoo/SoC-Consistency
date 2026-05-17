"""Failure Mode and Effects Analysis (FMEA) blast-radius engine.

Given a regulator or device node name, simulates a complete power failure and
returns the *blast radius* — the transitive set of regulators, devices, and
clock paths that cascade offline as a result.

Usage from CLI:
    socc simulate failure vcc_3v3_sys board.dts
Usage from Python:
    from socc.fmea import simulate_failure
    report = simulate_failure(model, "vcc_3v3_sys")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from socc.model import SoC


# ── Keywords for node classification ─────────────────────────────────────────

_BOOT_CRITICAL: FrozenSet[str] = frozenset({
    "cpu", "arm", "a55", "a76", "a78", "cortex", "cluster",
    "ddr", "dram", "lpddr", "sdram", "memory",
    "emmc", "spi-nor", "nor-flash", "boot",
    "pmic", "pmu",
})
_CPU_KEYWORDS:  FrozenSet[str] = frozenset({"cpu", "arm", "a55", "a76", "a78", "cortex", "cluster"})
_DDR_KEYWORDS:  FrozenSet[str] = frozenset({"ddr", "dram", "lpddr", "sdram", "mem"})
_BUS_KEYWORDS:  FrozenSet[str] = frozenset({"i2c", "spi", "uart", "pcie", "usb", "sdmmc", "ufs"})


def _kw(name: str, kws: FrozenSet[str]) -> bool:
    n = name.lower()
    return any(k in n for k in kws)


# ── Report data structures ────────────────────────────────────────────────────


@dataclass
class CascadeStep:
    """A single node in the failure propagation chain."""
    name: str
    node_type: str          # "regulator" | "device" | "clock"
    failure_mode: str       # human-readable
    depth: int              # cascade depth from origin (0 = origin)


@dataclass
class FMEAReport:
    """Complete blast-radius report for a single failure event."""

    # ── Input ─────────────────────────────────────────────────────────────────
    failed_node: str
    failed_node_type: str           # "regulator" | "device" | "unknown"

    # ── Power cascade ─────────────────────────────────────────────────────────
    cascading_power_failures: List[str] = field(default_factory=list)
    # Regulators that lose power as a direct consequence of the failure

    # ── Device impact ─────────────────────────────────────────────────────────
    offline_devices: List[str] = field(default_factory=list)
    # Device names that go offline (supplied by affected regulators)

    # ── Clock impact ──────────────────────────────────────────────────────────
    frozen_clocks: List[str] = field(default_factory=list)
    # Clock signals whose provider is among the offline devices

    # ── Bus impact ────────────────────────────────────────────────────────────
    affected_buses: List[str] = field(default_factory=list)
    # I2C, SPI, PCIe etc. controllers that go offline

    # ── Classification ────────────────────────────────────────────────────────
    severity: str = "UNKNOWN"       # "SAFE" | "DEGRADED" | "CRITICAL" | "FATAL"
    is_cpu_affected: bool = False
    is_ddr_affected: bool = False
    is_boot_critical: bool = False

    # ── Full chain for display ────────────────────────────────────────────────
    cascade_steps: List[CascadeStep] = field(default_factory=list)


# ── Graph helpers ─────────────────────────────────────────────────────────────


def _regulator_subtree(soc: SoC, root: str) -> List[str]:
    """BFS: return root + all regulators reachable *downstream* from root."""
    visited: List[str] = []
    queue: List[str] = [root]
    seen: Set[str] = set()
    pt = soc.power_tree

    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        visited.append(cur)

        # children via edge list
        for child in pt.edges.get(cur, []):
            if child not in seen:
                queue.append(child)

        # children via parent pointer (handles sparse edge population)
        for reg_name, reg in pt.nodes.items():
            if reg.parent == cur and reg_name not in seen:
                queue.append(reg_name)

    return visited


def _devices_on_failed_rails(soc: SoC, failed_regs: List[str]) -> List[str]:
    """Return devices whose supply chain includes any failed regulator."""
    failed_set = set(failed_regs)
    offline: List[str] = []

    # device_supplies map: device → list of supply phandles
    for dev, supplies in soc.device_supplies.items():
        for s in supplies:
            if s.strip().lstrip("&") in failed_set:
                if dev not in offline:
                    offline.append(dev)
                break

    # also honour consumers listed on the Regulator objects themselves
    for reg_name in failed_regs:
        reg = soc.power_tree.nodes.get(reg_name)
        if reg:
            for consumer in reg.consumers:
                c = consumer.strip().lstrip("&")
                if c and c not in offline and c in soc.devices:
                    offline.append(c)

    return offline


def _clocks_from_offline_devices(soc: SoC, offline_devices: List[str]) -> List[str]:
    """Return clock names whose provider is an offline device."""
    offline_set = set(offline_devices)
    return [
        clk_name
        for clk_name, clk in soc.clock_tree.clocks.items()
        if clk.provider in offline_set
    ]


def _assess_severity(report: FMEAReport) -> str:
    if report.is_cpu_affected or report.is_ddr_affected:
        return "FATAL"
    if report.is_boot_critical:
        return "CRITICAL"
    if report.cascading_power_failures or len(report.offline_devices) >= 3:
        return "CRITICAL" if len(report.offline_devices) >= 5 else "DEGRADED"
    if report.offline_devices or report.frozen_clocks:
        return "DEGRADED"
    return "SAFE"


# ── Public API ────────────────────────────────────────────────────────────────


def simulate_failure(soc: SoC, node_name: str) -> FMEAReport:
    """Simulate a total failure of *node_name* and return the blast-radius report.

    *node_name* can be:
    - A regulator name (from ``soc.power_tree.nodes``)
    - A device name (from ``soc.devices``)
    - A DTS path like ``/regulators/vcc_3v3_sys`` (resolved to a name)

    The function does **not** mutate the model.
    """
    # ── Resolve name ─────────────────────────────────────────────────────────
    # Normalize path → name
    lookup = node_name.strip("/").split("/")[-1]  # last path component

    if lookup in soc.power_tree.nodes:
        node_type: str = "regulator"
    elif lookup in soc.devices:
        node_type = "device"
    else:
        # Fuzzy: find closest match
        node_type = "unknown"
        for reg in soc.power_tree.nodes:
            if lookup in reg or reg in lookup:
                lookup = reg
                node_type = "regulator"
                break
        if node_type == "unknown":
            for dev in soc.devices:
                if lookup in dev or dev in lookup:
                    lookup = dev
                    node_type = "device"
                    break

    report = FMEAReport(failed_node=lookup, failed_node_type=node_type)

    # ── Power cascade ─────────────────────────────────────────────────────────
    if node_type == "regulator":
        all_failed = _regulator_subtree(soc, lookup)
        # Step 0 = the failed node itself; steps ≥1 are cascading
        for depth, reg in enumerate(all_failed):
            report.cascade_steps.append(
                CascadeStep(reg, "regulator", "loss-of-power", depth)
            )
        report.cascading_power_failures = all_failed[1:]   # exclude origin
        affected_devices = _devices_on_failed_rails(soc, all_failed)
    else:
        affected_devices = [lookup] if lookup in soc.devices else []

    # ── Device classification ─────────────────────────────────────────────────
    report.offline_devices = affected_devices
    for dev in affected_devices + [lookup]:
        if _kw(dev, _CPU_KEYWORDS):
            report.is_cpu_affected = True
            report.is_boot_critical = True
        if _kw(dev, _DDR_KEYWORDS):
            report.is_ddr_affected = True
            report.is_boot_critical = True
        if _kw(dev, _BOOT_CRITICAL):
            report.is_boot_critical = True
        if _kw(dev, _BUS_KEYWORDS):
            report.affected_buses.append(dev)

    # Also classify the failed regulator itself by name
    if _kw(lookup, _CPU_KEYWORDS):
        report.is_cpu_affected = True
        report.is_boot_critical = True
    if _kw(lookup, _DDR_KEYWORDS):
        report.is_ddr_affected = True
        report.is_boot_critical = True
    if _kw(lookup, _BOOT_CRITICAL):
        report.is_boot_critical = True

    # ── Clock cascade ─────────────────────────────────────────────────────────
    report.frozen_clocks = _clocks_from_offline_devices(soc, report.offline_devices)
    for clk in report.frozen_clocks:
        report.cascade_steps.append(
            CascadeStep(clk, "clock", "clock-freeze", len(report.cascade_steps))
        )

    # ── Overall severity ──────────────────────────────────────────────────────
    report.severity = _assess_severity(report)
    return report


def render_fmea_report(report: FMEAReport, use_color: bool = True) -> str:
    """Return a human-readable FMEA report string (ANSI colour optional)."""
    import click   # only for styling

    sev_color = {
        "FATAL":    ("red",     True),
        "CRITICAL": ("red",     False),
        "DEGRADED": ("yellow",  False),
        "SAFE":     ("green",   False),
    }.get(report.severity, ("white", False))

    lines: List[str] = []
    hdr = f"[FMEA REPORT]  Simulated failure: {report.failed_node!r}"
    if use_color:
        lines.append(click.style(hdr, fg=sev_color[0], bold=sev_color[1]))
    else:
        lines.append(hdr)

    lines.append("")
    lines.append(f"  Node type : {report.failed_node_type}")
    sev_label = report.severity
    if use_color:
        sev_label = click.style(report.severity, fg=sev_color[0], bold=True)
    lines.append(f"  Severity  : {sev_label}")
    lines.append("")

    # ── Power cascade ─────────────────────────────────────────────────────────
    if report.cascading_power_failures:
        lines.append("  Cascading Power Failures:")
        for r in report.cascading_power_failures:
            bullet = click.style("  ●", fg="red") if use_color else "  ●"
            lines.append(f"{bullet}  {r}  →  loses supply voltage")
        lines.append("")

    # ── Offline devices ───────────────────────────────────────────────────────
    if report.offline_devices:
        lines.append(f"  Offline Devices  ({len(report.offline_devices)} total):")
        for dev in report.offline_devices:
            icon = "💀" if _kw(dev, _CPU_KEYWORDS | _DDR_KEYWORDS) else "✗"
            if not use_color:
                icon = "X"
            lines.append(f"    {icon}  {dev}")
        lines.append("")

    # ── Frozen clocks ─────────────────────────────────────────────────────────
    if report.frozen_clocks:
        lines.append(f"  Frozen Clocks  ({len(report.frozen_clocks)} total):")
        for clk in report.frozen_clocks[:8]:
            lines.append(f"       {clk}")
        if len(report.frozen_clocks) > 8:
            lines.append(f"       … and {len(report.frozen_clocks)-8} more")
        lines.append("")

    # ── Affected buses ────────────────────────────────────────────────────────
    if report.affected_buses:
        lines.append(f"  Affected Buses: {', '.join(report.affected_buses)}")
        lines.append("")

    # ── Flags ─────────────────────────────────────────────────────────────────
    if report.is_cpu_affected:
        flag = click.style("  ⚠  CPU cluster may halt!", fg="red", bold=True) if use_color else "  !! CPU cluster may halt!"
        lines.append(flag)
    if report.is_ddr_affected:
        flag = click.style("  ⚠  DDR / DRAM will lose power — system crash imminent!", fg="red", bold=True) if use_color else "  !! DDR / DRAM will lose power — system crash imminent!"
        lines.append(flag)
    if report.is_boot_critical:
        flag = click.style("  ⚠  Boot-critical subsystem affected — system will NOT boot.", fg="red", bold=True) if use_color else "  !! Boot-critical subsystem affected — system will NOT boot."
        lines.append(flag)

    # ── Conclusion ────────────────────────────────────────────────────────────
    lines.append("")
    conclusions = {
        "FATAL":    "Failure in this supply is FATAL — the system cannot survive.",
        "CRITICAL": "Failure in this supply is CRITICAL — major subsystems will go offline.",
        "DEGRADED": "Failure causes DEGRADED operation — some peripherals will stop working.",
        "SAFE":     "Failure is SAFE — no critical subsystems are affected.",
    }
    conclusion = conclusions.get(report.severity, "Unknown impact.")
    if use_color:
        lines.append(click.style(f"  Conclusion: {conclusion}", fg=sev_color[0], bold=sev_color[1]))
    else:
        lines.append(f"  Conclusion: {conclusion}")

    return "\n".join(lines)
