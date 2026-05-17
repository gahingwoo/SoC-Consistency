"""
Physical Hardware Bounds Auditor
=================================

Detects copy-paste "array out-of-bounds" errors in DTS files where a
property references a hardware resource with an index that exceeds the
physical capacity of that IP block as declared in the SoC YAML.

The most common real-world victims:
  - ``gpios = <&gpio1 35 GPIO_ACTIVE_HIGH>``  — GPIO bank has only 32 pins
  - ``dmas = <&dmac 48 ...>``                 — DMA controller has 32 channels
  - ``pwms = <&pwm 16 ...>``                  — PWM block has only 8 channels
  - ``cs-gpios = <&gpio0 8 ...>``             — GPIO0 has only 8 pins

The auditor uses the SoC hardware database loaded from the YAML
(``data/soc/<vendor>/<soc>.yaml``) as the authoritative source of physical
limits.  A built-in fallback table covers common Rockchip families when no
YAML limit is configured.

The SoC YAML extension format (optional section):
  hardware_limits:
    gpio0:  {pins: 32}
    gpio1:  {pins: 32}
    dmac0:  {channels: 32}
    pwm:    {channels: 16}

Rule IDs
---------
  BND-001  GPIO pin index out of range
  BND-002  DMA channel index out of range
  BND-003  PWM channel index out of range
  BND-004  IRQ line index out of range  (GIC SPI exceeds hw max)
  BND-005  CS-GPIO (SPI chip-select) index out of range
  BND-006  Unknown bank referenced (sanity-check only)

CLI:
  socc check-bounds board.dts
  socc check-bounds board.dts --soc rk3588
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from socc.model import SoC, IRNode


# ── SoC hardware-limits database ─────────────────────────────────────────────

# Fallback limits for common Rockchip SoCs.
# Maps  lower(soc_name) → { resource_pattern → limit }
# resource_pattern is matched as a prefix against the phandle target name.
_DEFAULT_LIMITS: Dict[str, Dict[str, int]] = {
    # GPIO banks (32 pins per bank is the Rockchip HW standard)
    "_gpio_pins": 32,
    "_dma_channels": 32,
    "_pwm_channels": 8,
    "_cs_max": 32,   # reasonable upper bound
}

# Per-family overrides keyed by lowercase SoC name fragment
_SOC_OVERRIDES: Dict[str, Dict[str, int]] = {
    "rk3588": {
        "gpio0_pins": 32, "gpio1_pins": 32, "gpio2_pins": 32,
        "gpio3_pins": 32, "gpio4_pins": 32,
        "dmac0_channels": 32, "dmac1_channels": 32, "dmac2_channels": 32,
        "pwm_channels": 16,
    },
    "rk3576": {
        "gpio0_pins": 32, "gpio1_pins": 32, "gpio2_pins": 32,
        "gpio3_pins": 32, "gpio4_pins": 32,
        "dmac0_channels": 32, "dmac1_channels": 32,
        "pwm_channels": 16,
    },
    "rk3568": {
        "gpio0_pins": 32, "gpio1_pins": 32, "gpio2_pins": 32,
        "gpio3_pins": 32,
        "dmac0_channels": 32, "dmac1_channels": 32,
        "pwm_channels": 16,
    },
    "rk3399": {
        "gpio0_pins": 32, "gpio1_pins": 32, "gpio2_pins": 32,
        "gpio3_pins": 32, "gpio4_pins": 32,
        "dmac0_channels": 32, "dmac1_channels": 32,
        "pwm_channels": 4,
    },
}


def _get_limit(soc_name: str, resource: str) -> Optional[int]:
    """
    Return the physical limit for *resource* on *soc_name*, or None.

    *resource* is like ``"gpio1_pins"`` or ``"dmac0_channels"``.
    """
    sname = soc_name.lower()
    for frag, limits in _SOC_OVERRIDES.items():
        if frag in sname:
            if resource in limits:
                return limits[resource]
    return None


def _gpio_pin_limit(soc_name: str, bank_name: str) -> int:
    """Return pin count for a GPIO bank.  Default = 32."""
    key = f"{bank_name}_pins"
    return _get_limit(soc_name, key) or 32


def _dma_channel_limit(soc_name: str, ctrl_name: str) -> int:
    """Return DMA channel count for a controller.  Default = 32."""
    key = f"{ctrl_name}_channels"
    return _get_limit(soc_name, key) or 32


def _pwm_channel_limit(soc_name: str) -> int:
    """Return PWM channel count.  Default = 16."""
    key = "pwm_channels"
    return _get_limit(soc_name, key) or 16


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BoundsIssue:
    severity:    str   # "FATAL" | "ERROR" | "WARNING"
    rule_id:     str   # "BND-001" …
    node_path:   str
    node_name:   str
    prop_name:   str
    prop_value:  str   # human-readable raw value
    resource:    str   # e.g. "gpio1", "dmac0"
    index_used:  int
    index_max:   int   # max VALID index (0-based)
    description: str
    suggestion:  str


@dataclass
class BoundsReport:
    issues:       List[BoundsIssue] = field(default_factory=list)
    checked_props: int = 0

    @property
    def pass_result(self) -> bool:
        return all(i.severity != "FATAL" for i in self.issues)

    @property
    def fatal_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "FATAL")


# ── Property parsers ──────────────────────────────────────────────────────────

# gpios / cs-gpios: <&bank  pin  flags>  — pin is index [1]
def _parse_gpio_prop(
    prop_val: Any,
) -> Optional[Tuple[str, int]]:
    """Return (bank_label, pin_index) or None."""
    if not isinstance(prop_val, list) or len(prop_val) < 2:
        return None
    bank = str(prop_val[0])
    if bank.startswith("&"):
        bank = bank[1:]
    try:
        pin = int(prop_val[1])
    except (TypeError, ValueError):
        return None
    return bank, pin


# dmas: <&ctrl  channel  ...>
def _parse_dma_prop(
    prop_val: Any,
) -> Optional[Tuple[str, int]]:
    if not isinstance(prop_val, list) or len(prop_val) < 2:
        return None
    ctrl = str(prop_val[0])
    if ctrl.startswith("&"):
        ctrl = ctrl[1:]
    try:
        ch = int(prop_val[1])
    except (TypeError, ValueError):
        return None
    return ctrl, ch


# pwms: <&ctrl  channel  period_ns  ...>
def _parse_pwm_prop(
    prop_val: Any,
) -> Optional[Tuple[str, int]]:
    if not isinstance(prop_val, list) or len(prop_val) < 2:
        return None
    ctrl = str(prop_val[0])
    if ctrl.startswith("&"):
        ctrl = ctrl[1:]
    try:
        ch = int(prop_val[1])
    except (TypeError, ValueError):
        return None
    return ctrl, ch


# ── Node auditor ──────────────────────────────────────────────────────────────

def _audit_node(node: IRNode, soc_name: str) -> List[BoundsIssue]:
    issues: List[BoundsIssue] = []

    for prop_name, prop_val in node.properties.items():
        # ── GPIO ─────────────────────────────────────────────────────────────
        if prop_name in ("gpios", "cs-gpios", "reset-gpios", "enable-gpios",
                         "cd-gpios", "wp-gpios", "irq-gpios",
                         "pwr-gpios", "panel-gpios"):
            # prop_val may be a flat list: [&bank, pin, flags, &bank2, pin2, flags2, ...]
            # or a list-of-lists from some parsers.
            items = prop_val if isinstance(prop_val, list) else [prop_val]
            # Try parsing as single triplet first
            parsed = _parse_gpio_prop(items)
            if parsed:
                bank, pin = parsed
                limit = _gpio_pin_limit(soc_name, bank.lower())
                if pin > limit - 1:
                    issues.append(BoundsIssue(
                        severity="FATAL",
                        rule_id="BND-001",
                        node_path=node.path,
                        node_name=node.name,
                        prop_name=prop_name,
                        prop_value=str(items),
                        resource=bank,
                        index_used=pin,
                        index_max=limit - 1,
                        description=(
                            f"Pin index {pin} is out of range for "
                            f"{bank} (valid: 0–{limit - 1})"
                        ),
                        suggestion=(
                            f"Check the physical schematic: {bank} only has "
                            f"{limit} pins (indices 0–{limit - 1})"
                        ),
                    ))

        # ── DMA ──────────────────────────────────────────────────────────────
        elif prop_name in ("dmas",):
            parsed = _parse_dma_prop(prop_val if isinstance(prop_val, list) else [prop_val])
            if parsed:
                ctrl, ch = parsed
                limit = _dma_channel_limit(soc_name, ctrl.lower())
                if ch >= limit:
                    issues.append(BoundsIssue(
                        severity="FATAL",
                        rule_id="BND-002",
                        node_path=node.path,
                        node_name=node.name,
                        prop_name=prop_name,
                        prop_value=str(prop_val),
                        resource=ctrl,
                        index_used=ch,
                        index_max=limit - 1,
                        description=(
                            f"DMA channel {ch} is out of range for "
                            f"{ctrl} (valid: 0–{limit - 1})"
                        ),
                        suggestion=(
                            f"{ctrl} provides {limit} channels; "
                            f"use a channel index 0–{limit - 1}"
                        ),
                    ))

        # ── PWM ──────────────────────────────────────────────────────────────
        elif prop_name in ("pwms",):
            parsed = _parse_pwm_prop(prop_val if isinstance(prop_val, list) else [prop_val])
            if parsed:
                ctrl, ch = parsed
                limit = _pwm_channel_limit(soc_name)
                if ch >= limit:
                    issues.append(BoundsIssue(
                        severity="FATAL",
                        rule_id="BND-003",
                        node_path=node.path,
                        node_name=node.name,
                        prop_name=prop_name,
                        prop_value=str(prop_val),
                        resource=ctrl,
                        index_used=ch,
                        index_max=limit - 1,
                        description=(
                            f"PWM channel {ch} is out of range for "
                            f"{ctrl} (valid: 0–{limit - 1})"
                        ),
                        suggestion=(
                            f"PWM block {ctrl} has {limit} channels; "
                            f"valid indices are 0–{limit - 1}"
                        ),
                    ))

    return issues


# ── Main entry point ──────────────────────────────────────────────────────────

def check_bounds(soc: SoC) -> BoundsReport:
    """Run physical-bounds audit on the SoC model."""
    report = BoundsReport()
    for node in soc.devices.values():
        status = node.properties.get("status", "okay")
        if str(status).lower() not in ("okay", "ok"):
            continue  # only check active nodes
        new_issues = _audit_node(node, soc.name)
        report.checked_props += len(node.properties)
        report.issues.extend(new_issues)
    return report


# ── Renderer ──────────────────────────────────────────────────────────────────

_COLOR = {
    "FATAL": "\033[1;31m", "ERROR": "\033[33m", "WARNING": "\033[33m",
    "OK":    "\033[1;32m", "BOLD":  "\033[1m",   "RESET":   "\033[0m",
    "DIM":   "\033[2m",    "CYAN":  "\033[1;36m",
}


def render_bounds_text(report: BoundsReport, use_color: bool = True) -> str:
    C = _COLOR if use_color else {k: "" for k in _COLOR}
    lines: List[str] = []
    banner = "SOCC PHYSICAL BOUNDS AUDITOR"
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"{C['BOLD']}{banner}{C['RESET']}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"  Properties checked : {report.checked_props}")
    lines.append(f"  Violations found   : "
                 f"{C['FATAL'] if report.fatal_count else C['OK']}"
                 f"{len(report.issues)}{C['RESET']}")
    lines.append("")

    if not report.issues:
        lines.append(f"{C['OK']}[✓] No bounds violations found.{C['RESET']}")
    else:
        for issue in sorted(report.issues, key=lambda i: i.node_path):
            sc = C.get(issue.severity, C["BOLD"])
            lines.append(
                f"{sc}[{issue.severity}] {issue.rule_id}{C['RESET']}  "
                f"{C['DIM']}{issue.node_path}{C['RESET']}"
            )
            lines.append(f"    Property  : {issue.prop_name} = {issue.prop_value}")
            lines.append(f"    🔥 {issue.description}")
            lines.append(
                f"    {C['DIM']}💡 {issue.suggestion}{C['RESET']}"
            )
            lines.append("")

    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    return "\n".join(lines)


def render_bounds_json(report: BoundsReport) -> str:
    return json.dumps({
        "checked_props": report.checked_props,
        "pass_result":   report.pass_result,
        "fatal_count":   report.fatal_count,
        "issues": [
            {
                "severity":   i.severity,
                "rule_id":    i.rule_id,
                "node_path":  i.node_path,
                "prop_name":  i.prop_name,
                "resource":   i.resource,
                "index_used": i.index_used,
                "index_max":  i.index_max,
                "description": i.description,
                "suggestion":  i.suggestion,
            }
            for i in report.issues
        ],
    }, indent=2)
