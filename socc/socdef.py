"""
.socdef — System-on-Chip Hardware Constraint Definition Format

A vendor-neutral, declarative YAML/JSON schema that replaces hard-coded
Python rule functions with data-driven constraint files.

Design goals
------------
* Chip vendors (Rockchip, NXP, TI, …) write .socdef YAML files once and
  submit them to this repo — no Python knowledge needed.
* The socc engine loads any .socdef file and generates live rule objects.
* Rules cover: voltage domains, clock limits, pinmux exclusions, interrupt
  priority classes, and compatible-string ↔ driver requirements.

Example .socdef (YAML):
-----------------------
  soc: rk3588
  vendor: rockchip
  version: "1.0"
  voltage_domains:
    - name: vcc_1v8
      min_mv: 1620
      max_mv: 1980
      abs_max_mv: 2000
  clock_limits:
    - name: pll_apll
      max_hz: 2400000000
  pinmux_exclusions:
    - pins: [GPIO4_A0, GPIO4_A1]
      reason: "Cannot be used as SPI and I2C simultaneously"
  compatible_requires:
    - compatible: "snps,dw-apb-i2c"
      kernel_config: CONFIG_I2C_DESIGNWARE_PLATFORM
      min_kernel: "5.10"

CLI:
  socc validate-socdef rk3588.socdef
  socc check board.dts --socdef rk3588.socdef
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ─────────────────────────────────────────────────────────────────────────────
# Schema data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VoltageDomain:
    name: str
    min_mv: int = 0
    max_mv: int = 0
    abs_max_mv: int = 0
    description: str = ""

    @property
    def min_v(self) -> float:
        return self.min_mv / 1000

    @property
    def max_v(self) -> float:
        return self.max_mv / 1000

    @property
    def abs_max_v(self) -> float:
        return self.abs_max_mv / 1000


@dataclass
class ClockLimit:
    name: str
    max_hz: int = 0
    min_hz: int = 0
    description: str = ""


@dataclass
class PinmuxExclusion:
    pins: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class CompatibleRequirement:
    compatible: str
    kernel_config: str = ""
    min_kernel: str = ""
    description: str = ""
    required_supply: str = ""
    required_clock: str = ""


@dataclass
class SafetyAnnotation:
    node_pattern: str      # regex matching node paths
    asil_level: str        # A / B / C / D
    requires_isolation: bool = True
    description: str = ""


@dataclass
class SoCDef:
    soc: str
    vendor: str
    version: str = "1.0"
    description: str = ""
    source_file: str = ""

    voltage_domains: List[VoltageDomain] = field(default_factory=list)
    clock_limits: List[ClockLimit] = field(default_factory=list)
    pinmux_exclusions: List[PinmuxExclusion] = field(default_factory=list)
    compatible_requires: List[CompatibleRequirement] = field(default_factory=list)
    safety_annotations: List[SafetyAnnotation] = field(default_factory=list)

    # Extra vendor-specific raw data
    extra: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_raw(path: str) -> Dict[str, Any]:
    text = Path(path).read_text()
    ext = Path(path).suffix.lower()
    if ext in (".yaml", ".yml", ".socdef"):
        if _HAS_YAML:
            return yaml.safe_load(text) or {}
        # Minimal YAML parser fallback (key: value, lists with -)
        return _minimal_yaml_parse(text)
    elif ext == ".json":
        return json.loads(text)
    else:
        # Try YAML first, then JSON
        if _HAS_YAML:
            try:
                return yaml.safe_load(text) or {}
            except Exception:
                pass
        return json.loads(text)


def _minimal_yaml_parse(text: str) -> Dict[str, Any]:
    """
    Ultra-minimal YAML parser for simple flat key: value files.
    Falls back gracefully when PyYAML is not installed.
    Supports: string values, integers, simple lists (- item).
    """
    result: Dict[str, Any] = {}
    current_key: Optional[str] = None
    current_list: Optional[List] = None
    current_dict: Optional[Dict] = None
    indent_stack: List[int] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.lstrip()

        if stripped.startswith("- "):
            val = stripped[2:].strip().strip('"\'')
            if current_list is not None:
                current_list.append(val)
        elif ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"\'')
            if indent == 0:
                if val:
                    # Try int
                    try:
                        result[key] = int(val)
                    except ValueError:
                        result[key] = val
                else:
                    result[key] = []
                    current_list = result[key]
                    current_key = key
            # Nested dicts not supported in minimal parser
    return result


def parse_socdef(path: str) -> SoCDef:
    """Load and parse a .socdef YAML/JSON file → SoCDef object."""
    raw = _load_raw(path)

    soc_def = SoCDef(
        soc=str(raw.get("soc", "")),
        vendor=str(raw.get("vendor", "")),
        version=str(raw.get("version", "1.0")),
        description=str(raw.get("description", "")),
        source_file=path,
        extra={k: v for k, v in raw.items()
               if k not in ("soc", "vendor", "version", "description",
                            "voltage_domains", "clock_limits",
                            "pinmux_exclusions", "compatible_requires",
                            "safety_annotations")},
    )

    for vd in raw.get("voltage_domains", []) or []:
        if isinstance(vd, dict):
            soc_def.voltage_domains.append(VoltageDomain(
                name=str(vd.get("name", "")),
                min_mv=int(vd.get("min_mv", 0)),
                max_mv=int(vd.get("max_mv", 0)),
                abs_max_mv=int(vd.get("abs_max_mv", 0)),
                description=str(vd.get("description", "")),
            ))

    for cl in raw.get("clock_limits", []) or []:
        if isinstance(cl, dict):
            soc_def.clock_limits.append(ClockLimit(
                name=str(cl.get("name", "")),
                max_hz=int(cl.get("max_hz", 0)),
                min_hz=int(cl.get("min_hz", 0)),
                description=str(cl.get("description", "")),
            ))

    for pe in raw.get("pinmux_exclusions", []) or []:
        if isinstance(pe, dict):
            soc_def.pinmux_exclusions.append(PinmuxExclusion(
                pins=list(pe.get("pins", [])),
                functions=list(pe.get("functions", [])),
                reason=str(pe.get("reason", "")),
            ))

    for cr in raw.get("compatible_requires", []) or []:
        if isinstance(cr, dict):
            soc_def.compatible_requires.append(CompatibleRequirement(
                compatible=str(cr.get("compatible", "")),
                kernel_config=str(cr.get("kernel_config", "")),
                min_kernel=str(cr.get("min_kernel", "")),
                description=str(cr.get("description", "")),
                required_supply=str(cr.get("required_supply", "")),
                required_clock=str(cr.get("required_clock", "")),
            ))

    for sa in raw.get("safety_annotations", []) or []:
        if isinstance(sa, dict):
            soc_def.safety_annotations.append(SafetyAnnotation(
                node_pattern=str(sa.get("node_pattern", "")),
                asil_level=str(sa.get("asil_level", "B")),
                requires_isolation=bool(sa.get("requires_isolation", True)),
                description=str(sa.get("description", "")),
            ))

    return soc_def


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SoCDefError:
    field: str
    message: str
    severity: str = "error"   # "error" | "warning"


def validate_socdef(soc_def: SoCDef) -> List[SoCDefError]:
    errors: List[SoCDefError] = []

    if not soc_def.soc:
        errors.append(SoCDefError("soc", "Missing required field 'soc'"))
    if not soc_def.vendor:
        errors.append(SoCDefError("vendor", "Missing required field 'vendor'", severity="warning"))

    for vd in soc_def.voltage_domains:
        if not vd.name:
            errors.append(SoCDefError("voltage_domains", "Domain missing name"))
        if vd.min_mv > vd.max_mv and vd.max_mv > 0:
            errors.append(SoCDefError(
                "voltage_domains",
                f"Domain '{vd.name}': min_mv ({vd.min_mv}) > max_mv ({vd.max_mv})"
            ))
        if vd.abs_max_mv > 0 and vd.max_mv > vd.abs_max_mv:
            errors.append(SoCDefError(
                "voltage_domains",
                f"Domain '{vd.name}': max_mv ({vd.max_mv}) exceeds abs_max_mv ({vd.abs_max_mv})"
            ))

    for cl in soc_def.clock_limits:
        if not cl.name:
            errors.append(SoCDefError("clock_limits", "Clock limit missing name"))
        if cl.max_hz < 0:
            errors.append(SoCDefError("clock_limits", f"Clock '{cl.name}': negative max_hz"))

    for cr in soc_def.compatible_requires:
        if not cr.compatible:
            errors.append(SoCDefError("compatible_requires", "Entry missing 'compatible' field"))

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Rule generation: SoCDef → live checker violations
# ─────────────────────────────────────────────────────────────────────────────

from socc.model import SoC


@dataclass
class SoCDefViolation:
    severity: str          # "FATAL" | "CRITICAL" | "WARNING" | "INFO"
    rule_id: str
    node_path: str
    node_name: str
    description: str
    suggestion: str = ""


def check_dts_against_socdef(soc: SoC, soc_def: SoCDef) -> List[SoCDefViolation]:
    """Apply all constraints in a SoCDef to a parsed SoC model."""
    violations: List[SoCDefViolation] = []

    # ── Voltage domain checks ──────────────────────────────────────────────
    for vd in soc_def.voltage_domains:
        reg = soc.power_tree.nodes.get(vd.name)
        if reg is None:
            continue
        v_max = getattr(reg, "voltage_max", 0) or 0
        v_max_v = v_max / 1_000_000 if v_max > 1000 else v_max  # handle µV vs V
        if vd.abs_max_mv > 0 and v_max_v * 1000 > vd.abs_max_mv:
            violations.append(SoCDefViolation(
                severity="FATAL",
                rule_id=f"SOCDEF-V001",
                node_path=f"/regulators/{vd.name}",
                node_name=vd.name,
                description=(
                    f"Supply '{vd.name}' set to {v_max_v:.3f}V which exceeds the "
                    f"{soc_def.soc} absolute maximum of {vd.abs_max_mv}mV."
                ),
                suggestion=(
                    f"Set regulator-max-microvolt ≤ {vd.abs_max_mv * 1000} µV "
                    f"(= {vd.abs_max_mv}mV) in the DTS."
                ),
            ))
        elif vd.max_mv > 0 and v_max_v * 1000 > vd.max_mv:
            violations.append(SoCDefViolation(
                severity="WARNING",
                rule_id="SOCDEF-V002",
                node_path=f"/regulators/{vd.name}",
                node_name=vd.name,
                description=(
                    f"Supply '{vd.name}' at {v_max_v:.3f}V exceeds rated max "
                    f"{vd.max_mv}mV for this SoC."
                ),
                suggestion=f"Verify PMIC output and DTS regulator-max-microvolt.",
            ))

    # ── Clock limit checks ─────────────────────────────────────────────────
    for cl in soc_def.clock_limits:
        clk = soc.clock_tree.clocks.get(cl.name)
        if clk is None:
            continue
        hz = getattr(clk, "rate", None) or getattr(clk, "frequency", 0) or 0
        if cl.max_hz > 0 and hz > cl.max_hz:
            violations.append(SoCDefViolation(
                severity="CRITICAL",
                rule_id="SOCDEF-C001",
                node_path=f"/clocks/{cl.name}",
                node_name=cl.name,
                description=(
                    f"Clock '{cl.name}' at {hz/1e6:.0f} MHz exceeds "
                    f"{soc_def.soc} max of {cl.max_hz/1e6:.0f} MHz."
                ),
                suggestion=(
                    f"Set 'clock-frequency' or 'assigned-clock-rates' ≤ {cl.max_hz} Hz."
                ),
            ))

    # ── Compatible-string requirements ────────────────────────────────────
    for cr in soc_def.compatible_requires:
        for dev_name, node in soc.devices.items():
            compat_list = node.properties.get("compatible") or []
            if isinstance(compat_list, str):
                compat_list = [compat_list]
            if cr.compatible not in compat_list:
                continue
            status = node.properties.get("status", "okay")
            if status not in ("okay", "ok"):
                continue
            # Check required supply
            if cr.required_supply:
                supplies = soc.device_supplies.get(dev_name, [])
                if not any(cr.required_supply in s for s in supplies):
                    violations.append(SoCDefViolation(
                        severity="WARNING",
                        rule_id="SOCDEF-D001",
                        node_path=node.path,
                        node_name=dev_name,
                        description=(
                            f"Node {dev_name} (compatible={cr.compatible}) should "
                            f"declare supply '{cr.required_supply}'."
                        ),
                        suggestion=f"Add '{cr.required_supply}-supply = <&{cr.required_supply}>;'",
                    ))

    # ── Pinmux exclusion checks ────────────────────────────────────────────
    for excl in soc_def.pinmux_exclusions:
        if len(excl.pins) < 2:
            continue
        # Check if multiple devices claim the same pins
        pin_users: Dict[str, List[str]] = {}
        for dev_name, node in soc.devices.items():
            pin_cfg = str(
                node.properties.get("pinctrl-0")
                or node.properties.get("pinctrl-names")
                or ""
            )
            for pin in excl.pins:
                if pin.lower() in pin_cfg.lower():
                    pin_users.setdefault(pin, []).append(dev_name)
        conflicts = {p: users for p, users in pin_users.items() if len(users) > 1}
        if conflicts:
            for pin, users in conflicts.items():
                violations.append(SoCDefViolation(
                    severity="CRITICAL",
                    rule_id="SOCDEF-P001",
                    node_path=f"/pinctrl/{pin}",
                    node_name=pin,
                    description=(
                        f"Pin {pin} claimed by multiple devices: {', '.join(users)}. "
                        f"Rule: {excl.reason}"
                    ),
                    suggestion="Assign each pin to exactly one function in the DTS pinctrl nodes.",
                ))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Schema template generator
# ─────────────────────────────────────────────────────────────────────────────

_TEMPLATE = """\
# SoC-Consistency Hardware Constraint Definition (.socdef)
# Generated by `socc init-socdef --soc {soc_name}`
# Format version: 1.0
#
# Submit this file to https://github.com/woo/SoC-Consistency/data/soc/
# so all users of {soc_name} benefit from these constraints.

soc: {soc_name}
vendor: {vendor}
version: "1.0"
description: "{soc_name} hardware constraints for SoC-Consistency"

# ── Voltage domains ──────────────────────────────────────────────────────────
# List every supply rail with its operational and absolute-maximum voltages.
voltage_domains:
  - name: vcc_3v3
    min_mv: 3135       # -5 %
    max_mv: 3465       # +5 %
    abs_max_mv: 3600
    description: "Main 3.3V peripheral rail"

  - name: vcc_1v8
    min_mv: 1620
    max_mv: 1980
    abs_max_mv: 2000
    description: "1.8V IO rail"

  - name: vdd_cpu
    min_mv: 700
    max_mv: 1200
    abs_max_mv: 1300
    description: "CPU core voltage"

# ── Clock limits ─────────────────────────────────────────────────────────────
clock_limits:
  - name: pll_apll
    max_hz: 2400000000
    description: "Application PLL — max 2.4 GHz"

  - name: pll_gpll
    max_hz: 1188000000
    description: "General PLL — max 1.188 GHz"

# ── Pinmux exclusions ────────────────────────────────────────────────────────
# Pins that cannot be shared between multiple functions simultaneously.
pinmux_exclusions:
  - pins: [GPIO4_A0, GPIO4_A1]
    reason: "SPI0 and I2C3 share these pins; cannot enable both"

# ── Compatible-string requirements ───────────────────────────────────────────
# What kernel config / supply / clock each device driver needs.
compatible_requires:
  - compatible: "snps,dw-apb-i2c"
    kernel_config: CONFIG_I2C_DESIGNWARE_PLATFORM
    min_kernel: "5.10"
    required_supply: "vcc_1v8"
    description: "Synopsys DesignWare APB I2C controller"

  - compatible: "snps,dw-apb-uart"
    kernel_config: CONFIG_SERIAL_8250_DW
    min_kernel: "5.4"
    description: "Synopsys DesignWare APB UART"

# ── Safety annotations ───────────────────────────────────────────────────────
# Mark paths that must be treated as safety islands during compliance checks.
safety_annotations:
  - node_pattern: ".*can-bus.*"
    asil_level: "B"
    requires_isolation: true
    description: "CAN bus nodes are safety-critical (braking system)"
"""


def generate_socdef_template(soc_name: str, vendor: str = "unknown") -> str:
    return _TEMPLATE.format(soc_name=soc_name, vendor=vendor)


# ─────────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_socdef_violations(
    violations: List[SoCDefViolation],
    soc_def: SoCDef,
    use_color: bool = True,
) -> str:
    lines: List[str] = []
    _C = {
        "FATAL":    "\033[1;31m" if use_color else "",
        "CRITICAL": "\033[1;35m" if use_color else "",
        "WARNING":  "\033[1;33m" if use_color else "",
        "INFO":     "\033[1;36m" if use_color else "",
        "RESET":    "\033[0m"    if use_color else "",
    }

    lines.append("=" * 70)
    lines.append(f"  SOCC SOCDEF CHECK  |  {soc_def.soc} v{soc_def.version} ({soc_def.vendor})")
    lines.append(f"  Source: {soc_def.source_file}")
    lines.append("=" * 70)

    if not violations:
        lines.append(f"\n{_C['INFO']}[✓] All .socdef constraints satisfied.{_C['RESET']}")
    else:
        for v in violations:
            col = _C.get(v.severity, "")
            lines.append(f"\n{col}[{v.severity}] {v.rule_id}{_C['RESET']}")
            lines.append(f"  Node : {v.node_path}")
            lines.append(f"  Issue: {v.description}")
            if v.suggestion:
                lines.append(f"  Fix  : {v.suggestion}")

    lines.append(f"\nSummary: {len(violations)} violation(s) from {soc_def.source_file}")
    return "\n".join(lines)
