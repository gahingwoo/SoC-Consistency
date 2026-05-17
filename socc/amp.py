"""Asymmetric Multi-Processing (AMP) cross-core conflict detector.

Usage:
    socc amp-audit linux.dts zephyr.dts

Two separate OS images can conflict over the same hardware resources when
they are deployed in an AMP configuration (e.g., Linux + Zephyr on an
RK3588 big/LITTLE cluster, or Linux + FreeRTOS on i.MX).

This module loads both DTS models independently, then detects:

AMP-001  Shared IRQ number used by both OS images
AMP-002  GPIO pin claimed by both OS images (same pin, different function)
AMP-003  Power domain Linux can gate that RTOS assumes always-on
AMP-004  Memory-mapped peripheral register range overlap
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from socc.model import SoC


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class AMPConflict:
    code: str               # "AMP-001", …
    severity: str           # "FATAL", "ERROR", "WARNING"
    resource_type: str      # "IRQ", "GPIO", "POWER_DOMAIN", "MMIO"
    resource_id: str        # human-readable resource name
    linux_node: str         # device name in Linux DTS
    rtos_node: str          # device name in RTOS DTS
    message: str
    impact: str
    suggestion: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_compat(node) -> str:
    compat = node.properties.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return " ".join(str(c) for c in compat).lower()
    return str(compat).lower()


def _is_enabled(node) -> bool:
    return node.properties.get("status", "okay") in ("okay", "ok", "")


def _parse_interrupts(node) -> List[int]:
    """Extract a flat list of interrupt numbers from the node's `interrupts` property."""
    irqs_raw = node.properties.get("interrupts", [])
    if isinstance(irqs_raw, (int, float)):
        return [int(irqs_raw)]
    result: List[int] = []
    if isinstance(irqs_raw, (list, tuple)):
        for v in irqs_raw:
            try:
                result.append(int(v))
            except (TypeError, ValueError):
                pass
    return result


def _parse_reg_range(node) -> Optional[Tuple[int, int]]:
    reg = node.properties.get("reg")
    if isinstance(reg, (list, tuple)) and len(reg) >= 2:
        try:
            return (int(reg[0]), int(reg[1]))
        except (TypeError, ValueError):
            pass
    if isinstance(reg, (int, float)):
        return (int(reg), 0)
    return None


def _ranges_overlap(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    a_end = a[0] + (a[1] or 4096)
    b_end = b[0] + (b[1] or 4096)
    return a[0] < b_end and b[0] < a_end


# ── AMP-001: Shared IRQ ───────────────────────────────────────────────────────


def _find_irq_conflicts(linux: SoC, rtos: SoC) -> List[AMPConflict]:
    conflicts: List[AMPConflict] = []

    linux_irq_map: Dict[int, str] = {}   # irq → device name
    for name, node in linux.devices.items():
        if not _is_enabled(node):
            continue
        for irq in _parse_interrupts(node):
            if irq > 0:
                linux_irq_map[irq] = name

    for rtos_name, rtos_node in rtos.devices.items():
        if not _is_enabled(rtos_node):
            continue
        for irq in _parse_interrupts(rtos_node):
            if irq in linux_irq_map:
                linux_name = linux_irq_map[irq]
                conflicts.append(AMPConflict(
                    code="AMP-001",
                    severity="FATAL",
                    resource_type="IRQ",
                    resource_id=f"IRQ {irq}",
                    linux_node=linux_name,
                    rtos_node=rtos_name,
                    message=(
                        f"IRQ {irq} is claimed by both Linux ({linux_name!r}) "
                        f"and RTOS ({rtos_name!r})."
                    ),
                    impact=(
                        "Both cores will receive the interrupt.  Whichever OS "
                        "handles it first may acknowledge the hardware, causing "
                        "the other OS to miss the event, enter a fault state, "
                        "or corrupt shared state."
                    ),
                    suggestion=(
                        f"Assign IRQ {irq} exclusively to one core/OS via the "
                        f"interrupt controller's routing configuration.  "
                        f"Alternatively, use inter-processor communication (RPMsg "
                        f"or OpenAMP) to proxy events from the IRQ owner to the "
                        f"other OS."
                    ),
                ))

    return conflicts


# ── AMP-002: GPIO pin conflict ────────────────────────────────────────────────


def _find_gpio_conflicts(linux: SoC, rtos: SoC) -> List[AMPConflict]:
    conflicts: List[AMPConflict] = []

    # Use pinmux_config which maps {pin_name: function}
    linux_pins = linux.pinmux_config   # pin → function
    rtos_pins  = rtos.pinmux_config

    shared = set(linux_pins) & set(rtos_pins)
    for pin in shared:
        lf = linux_pins[pin]
        rf = rtos_pins[pin]
        # Different functions on the same pin → hard conflict
        if lf != rf:
            conflicts.append(AMPConflict(
                code="AMP-002",
                severity="FATAL",
                resource_type="GPIO",
                resource_id=pin,
                linux_node=f"{pin}={lf}",
                rtos_node=f"{pin}={rf}",
                message=(
                    f"GPIO pin {pin!r} is configured differently: "
                    f"Linux expects function {lf!r}, RTOS expects {rf!r}."
                ),
                impact=(
                    "The physical pin multiplexer can only be in one state.  "
                    "Whichever OS writes the pinmux last will silently break "
                    "the other OS's peripheral."
                ),
                suggestion=(
                    f"Reserve pin {pin!r} for exactly one OS.  If both need "
                    f"the pin, use a firmware mailbox to request ownership "
                    f"before use, or route the signal through a bus-wide "
                    f"isolation cell."
                ),
            ))
        # Same function but both OS try to manage it — warn
        else:
            conflicts.append(AMPConflict(
                code="AMP-002",
                severity="WARNING",
                resource_type="GPIO",
                resource_id=pin,
                linux_node=f"{pin}={lf}",
                rtos_node=f"{pin}={rf}",
                message=(
                    f"GPIO pin {pin!r} (function {lf!r}) is claimed by "
                    f"both Linux and RTOS with the same function — potential "
                    f"double-initialisation."
                ),
                impact=(
                    "Two OS images will both attempt to configure the pinmux, "
                    "which may produce a race condition at boot."
                ),
                suggestion=(
                    f"Assign GPIO pin {pin!r} to exactly one OS.  The other "
                    f"should remove the pinctrl entry."
                ),
            ))

    return conflicts


# ── AMP-003: Power domain conflict ───────────────────────────────────────────


_ALWAYS_ON_KEYWORDS: FrozenSet[str] = frozenset({
    "always-on", "regulator-always-on", "wakeup-source",
})

_PM_KEYWORDS: FrozenSet[str] = frozenset({
    "pm-domains", "power-domains",
})


def _find_power_conflicts(linux: SoC, rtos: SoC) -> List[AMPConflict]:
    """Find power domains Linux may gate that RTOS assumes are always on."""
    conflicts: List[AMPConflict] = []

    # Devices in RTOS that have regulator-always-on or no power-domains annotation
    rtos_always_on_supplies: Set[str] = set()
    for name, node in rtos.devices.items():
        if not _is_enabled(node):
            continue
        for kw in _ALWAYS_ON_KEYWORDS:
            if kw in node.properties:
                rtos_always_on_supplies.add(name)

    # Supplies that Linux can turn off via runtime PM
    linux_pm_gated: Dict[str, str] = {}   # supply_name → device using PM
    for name, node in linux.devices.items():
        if not _is_enabled(node):
            continue
        props = node.properties
        if any(kw in props for kw in _PM_KEYWORDS):
            # Cross-check against RTOS device names / node names
            for rtos_name in rtos_always_on_supplies:
                if rtos_name in name or name in rtos_name:
                    linux_pm_gated[rtos_name] = name

    for rtos_dev, linux_dev in linux_pm_gated.items():
        conflicts.append(AMPConflict(
            code="AMP-003",
            severity="ERROR",
            resource_type="POWER_DOMAIN",
            resource_id=rtos_dev,
            linux_node=linux_dev,
            rtos_node=rtos_dev,
            message=(
                f"RTOS device {rtos_dev!r} is marked always-on but Linux "
                f"device {linux_dev!r} has Runtime PM that can gate the "
                f"same power domain."
            ),
            impact=(
                "If Linux powers down the domain via Runtime PM, the RTOS "
                "peripheral will lose power mid-operation, potentially "
                "causing a hard fault or memory corruption."
            ),
            suggestion=(
                f"Mark the shared power domain as 'regulator-always-on' "
                f"in the Linux DTS, or configure the power-domain controller "
                f"to refuse power-off requests while the RTOS is active."
            ),
        ))

    return conflicts


# ── AMP-004: MMIO overlap ─────────────────────────────────────────────────────


def _find_mmio_conflicts(linux: SoC, rtos: SoC) -> List[AMPConflict]:
    conflicts: List[AMPConflict] = []

    linux_ranges: List[Tuple[str, int, int]] = []
    for name, node in linux.devices.items():
        if _is_enabled(node):
            r = _parse_reg_range(node)
            if r:
                linux_ranges.append((name, r[0], r[1]))

    for rtos_name, rtos_node in rtos.devices.items():
        if not _is_enabled(rtos_node):
            continue
        rtos_r = _parse_reg_range(rtos_node)
        if not rtos_r:
            continue

        for linux_name, la, ls in linux_ranges:
            if _ranges_overlap((rtos_r[0], rtos_r[1]), (la, ls)):
                if linux_name == rtos_name:
                    continue  # same logical node — expected shared resource
                conflicts.append(AMPConflict(
                    code="AMP-004",
                    severity="ERROR",
                    resource_type="MMIO",
                    resource_id=f"0x{rtos_r[0]:x}",
                    linux_node=linux_name,
                    rtos_node=rtos_name,
                    message=(
                        f"MMIO range 0x{rtos_r[0]:x}+0x{rtos_r[1]:x} is mapped "
                        f"by both Linux ({linux_name!r}) and RTOS ({rtos_name!r})."
                    ),
                    impact=(
                        "Two OS images driving the same peripheral registers will "
                        "corrupt each other's state and may crash both cores."
                    ),
                    suggestion=(
                        f"Remove the device node from one OS DTS.  If both need "
                        f"access, implement an RPC protocol over shared memory "
                        f"(RPMsg / OpenAMP) where one OS owns the hardware and "
                        f"the other makes requests."
                    ),
                ))
                break  # one conflict per RTOS node is enough

    return conflicts


# ── Public API ────────────────────────────────────────────────────────────────


def amp_audit(linux_model: SoC, rtos_model: SoC) -> List[AMPConflict]:
    """Cross-reference *linux_model* and *rtos_model* for AMP conflicts."""
    conflicts: List[AMPConflict] = []
    conflicts.extend(_find_irq_conflicts(linux_model, rtos_model))
    conflicts.extend(_find_gpio_conflicts(linux_model, rtos_model))
    conflicts.extend(_find_power_conflicts(linux_model, rtos_model))
    conflicts.extend(_find_mmio_conflicts(linux_model, rtos_model))
    return conflicts


def render_amp_report(
    conflicts: List[AMPConflict],
    linux_path: str,
    rtos_path: str,
    use_color: bool = True,
) -> str:
    """Render a human-readable AMP audit report."""
    import click
    from pathlib import Path

    lines: List[str] = []
    hdr = (
        f"AMP Conflict Audit  ·  "
        f"Linux: {Path(linux_path).name}  ·  "
        f"RTOS: {Path(rtos_path).name}"
    )
    lines.append(click.style(hdr, fg="cyan", bold=True) if use_color else hdr)
    lines.append("")

    if not conflicts:
        ok_msg = "No AMP conflicts detected."
        lines.append(click.style(ok_msg, fg="green") if use_color else ok_msg)
        return "\n".join(lines)

    _sev_color = {"FATAL": "red", "ERROR": "magenta", "WARNING": "yellow"}

    for c in conflicts:
        tag = f"[{c.severity}] [{c.code}]"
        if use_color:
            tag = click.style(tag, fg=_sev_color.get(c.severity, "white"), bold=True)
        lines.append(f"{tag} {c.message}")
        lines.append(f"  Resource : {c.resource_id}")
        lines.append(f"  Impact   : {c.impact}")
        lines.append(f"  Fix      : {c.suggestion}")
        lines.append("")

    fatals   = sum(1 for c in conflicts if c.severity == "FATAL")
    errors   = sum(1 for c in conflicts if c.severity == "ERROR")
    warnings = sum(1 for c in conflicts if c.severity == "WARNING")
    summary  = f"Summary: {fatals} fatal · {errors} errors · {warnings} warnings"
    color = "red" if fatals else ("magenta" if errors else "yellow")
    lines.append(click.style(summary, fg=color, bold=True) if use_color else summary)

    return "\n".join(lines)
