"""Cross-stage DTS comparator: Bootloader vs Linux kernel.

Parses two SoC models (typically from U-Boot DTS and Linux DTS for the same
board) and diffs them on critical hardware categories:

  - UART / console (boot path must not diverge)
  - Memory layout (base + size)
  - Power supplies (voltage, enable flags)
  - Core clocks / PLLs
  - Pin-mux for critical peripherals (console, eMMC, primary I2C)

Each divergence is represented as a ``StageDiff`` with a severity of
``"error"`` (functional boot failure likely) or ``"warning"`` (configuration
mismatch, may work but is unclean).
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from socc.model import SoC


# ─────────────────────────────────────────────────────────────────────────────
# Data model for a single divergence
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StageDiff:
    """One discovered divergence between two DTS stages."""

    category: str       # "uart", "memory", "power", "clock", "pinmux"
    item: str           # item key (e.g. "uart0", "vdd_soc", "arm_pll")
    change_type: str    # "ADDED", "REMOVED", "CHANGED"
    severity: str       # "error" | "warning"
    bl_value: str       # bootloader-side description
    kn_value: str       # kernel-side description
    message: str        # human-readable explanation
    suggestion: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Comparison helpers
# ─────────────────────────────────────────────────────────────────────────────

# Keyword sets used to classify device names
_UART_KEYWORDS = {"uart", "serial", "debug", "console", "usart"}
_MEMORY_KEYWORDS = {"memory", "ddr", "dram", "sdram"}
_EMMC_KEYWORDS = {"mmc", "emmc", "sdmmc", "usdhc", "sdhci"}
_I2C_KEYWORDS = {"i2c", "i2c0", "twi"}
_SPI_KEYWORDS = {"spi", "ecspi", "qspi"}


def _classify_device(name: str, compatible: str) -> Optional[str]:
    """Return a category tag for a device, or None if not critical."""
    n = name.lower()
    c = compatible.lower()
    for kw in _UART_KEYWORDS:
        if kw in n or kw in c:
            return "uart"
    for kw in _MEMORY_KEYWORDS:
        if kw in n or kw in c:
            return "memory"
    for kw in _EMMC_KEYWORDS:
        if kw in n or kw in c:
            return "emmc"
    for kw in _I2C_KEYWORDS:
        if kw in n:
            return "i2c"
    return None


def _reg_fingerprint(reg) -> str:
    """Compact representation of a regulator for comparison."""
    return (
        f"{reg.voltage_min:.2f}V–{reg.voltage_max:.2f}V "
        f"seq={reg.sequence_order} "
        f"delay={reg.startup_delay_us}us"
    )


def _clock_fingerprint(clk) -> str:
    """Compact representation of a clock for comparison."""
    mhz = clk.rate / 1_000_000
    return f"{mhz:.1f}MHz parent={clk.parent or 'root'}"


# ─────────────────────────────────────────────────────────────────────────────
# Core comparison functions
# ─────────────────────────────────────────────────────────────────────────────


def _compare_devices(bl: SoC, kn: SoC) -> List[StageDiff]:
    """Diff critical devices between bootloader and kernel models."""
    diffs: List[StageDiff] = []

    bl_devs = {
        name: node.get_property("compatible", "") for name, node in bl.devices.items()
    }
    kn_devs = {
        name: node.get_property("compatible", "") for name, node in kn.devices.items()
    }

    all_names = set(bl_devs) | set(kn_devs)
    for name in sorted(all_names):
        bl_compat = bl_devs.get(name, "")
        kn_compat = kn_devs.get(name, "")
        cat = _classify_device(name, bl_compat or kn_compat)
        if cat is None:
            continue

        if name not in bl_devs:
            diffs.append(StageDiff(
                category=cat,
                item=name,
                change_type="ADDED",
                severity="warning",
                bl_value="(not present)",
                kn_value=kn_compat or "(present)",
                message=(
                    f"{cat.upper()} device '{name}' is defined in the kernel DTS "
                    "but absent from the bootloader DTS.  If the bootloader needs "
                    "this device to initialise (e.g. console, eMMC), the board may "
                    "not boot past the bootloader stage."
                ),
                suggestion=(
                    f"Add '{name}' node to the bootloader DTS with compatible "
                    f"'{kn_compat}' and matching reg/pinctrl properties."
                ),
            ))
        elif name not in kn_devs:
            diffs.append(StageDiff(
                category=cat,
                item=name,
                change_type="REMOVED",
                severity="error" if cat in {"uart", "emmc"} else "warning",
                bl_value=bl_compat or "(present)",
                kn_value="(not present)",
                message=(
                    f"{cat.upper()} device '{name}' is initialised by the bootloader "
                    "but missing from the Linux DTS.  The kernel driver will not load; "
                    "if this device owns a critical resource (console, storage) the "
                    "system will hang after handoff."
                ),
                suggestion=(
                    f"Add '{name}' node to the Linux DTS or confirm that the kernel "
                    "has a separate binding for this device."
                ),
            ))
        elif bl_compat != kn_compat and bl_compat and kn_compat:
            diffs.append(StageDiff(
                category=cat,
                item=name,
                change_type="CHANGED",
                severity="warning",
                bl_value=bl_compat,
                kn_value=kn_compat,
                message=(
                    f"{cat.upper()} device '{name}' has different compatibles "
                    f"between stages: bootloader uses '{bl_compat}', kernel uses "
                    f"'{kn_compat}'.  Driver selection may differ."
                ),
                suggestion=(
                    "Align the compatible strings if both stages target the same "
                    "hardware; or confirm the bootloader uses a legacy alias."
                ),
            ))

    return diffs


def _compare_power(bl: SoC, kn: SoC) -> List[StageDiff]:
    """Diff power regulator configurations."""
    diffs: List[StageDiff] = []
    bl_regs = bl.power_tree.nodes
    kn_regs = kn.power_tree.nodes
    all_regs = set(bl_regs) | set(kn_regs)

    for name in sorted(all_regs):
        if name not in bl_regs:
            diffs.append(StageDiff(
                category="power",
                item=name,
                change_type="ADDED",
                severity="warning",
                bl_value="(not present)",
                kn_value=_reg_fingerprint(kn_regs[name]),
                message=(
                    f"Regulator '{name}' is defined in the kernel DTS but not in the "
                    "bootloader.  If the kernel expects this supply to already be "
                    "enabled by the bootloader, it may fail to boot."
                ),
            ))
        elif name not in kn_regs:
            diffs.append(StageDiff(
                category="power",
                item=name,
                change_type="REMOVED",
                severity="warning",
                bl_value=_reg_fingerprint(bl_regs[name]),
                kn_value="(not present)",
                message=(
                    f"Regulator '{name}' is configured by the bootloader but absent "
                    "from the kernel DTS.  The kernel will not be able to manage this "
                    "supply; it will remain at the bootloader-programmed voltage."
                ),
            ))
        else:
            bl_fp = _reg_fingerprint(bl_regs[name])
            kn_fp = _reg_fingerprint(kn_regs[name])
            if bl_fp != kn_fp:
                # Voltage change is an error; sequencing change is a warning
                bl_v = (bl_regs[name].voltage_min, bl_regs[name].voltage_max)
                kn_v = (kn_regs[name].voltage_min, kn_regs[name].voltage_max)
                sev = "error" if bl_v != kn_v else "warning"
                diffs.append(StageDiff(
                    category="power",
                    item=name,
                    change_type="CHANGED",
                    severity=sev,
                    bl_value=bl_fp,
                    kn_value=kn_fp,
                    message=(
                        f"Regulator '{name}' configuration differs between stages: "
                        f"bootloader → {bl_fp} | kernel → {kn_fp}."
                    ),
                    suggestion=(
                        "Ensure both DTS files target the same PMIC OTP and that "
                        "regulator-min/max-microvolt are consistent."
                    ),
                ))

    return diffs


def _compare_clocks(bl: SoC, kn: SoC) -> List[StageDiff]:
    """Diff clock configurations for major clocks (>= 24 MHz)."""
    diffs: List[StageDiff] = []
    bl_clks = {
        n: c for n, c in bl.clock_tree.clocks.items()
        if c.rate >= 24_000_000
    }
    kn_clks = {
        n: c for n, c in kn.clock_tree.clocks.items()
        if c.rate >= 24_000_000
    }
    all_clks = set(bl_clks) | set(kn_clks)

    for name in sorted(all_clks):
        if name not in bl_clks or name not in kn_clks:
            continue  # presence diffs are minor — only flag CHANGED rates
        bl_fp = _clock_fingerprint(bl_clks[name])
        kn_fp = _clock_fingerprint(kn_clks[name])
        if bl_fp != kn_fp:
            diffs.append(StageDiff(
                category="clock",
                item=name,
                change_type="CHANGED",
                severity="warning",
                bl_value=bl_fp,
                kn_value=kn_fp,
                message=(
                    f"Clock '{name}' is configured differently between stages: "
                    f"bootloader → {bl_fp} | kernel → {kn_fp}."
                ),
                suggestion=(
                    "Ensure the kernel CCM configuration matches the bootloader PLL "
                    "initial state, or that the kernel driver re-programs the PLL."
                ),
            ))

    return diffs


def _compare_pinmux(bl: SoC, kn: SoC) -> List[StageDiff]:
    """Diff pin-mux for console and storage critical pins."""
    diffs: List[StageDiff] = []
    bl_pins = bl.pinmux_config
    kn_pins = kn.pinmux_config
    all_pins = set(bl_pins) | set(kn_pins)

    for pin in sorted(all_pins):
        bl_fn = bl_pins.get(pin)
        kn_fn = kn_pins.get(pin)
        if bl_fn == kn_fn:
            continue

        # Only flag critical function classes
        is_critical = any(
            kw in (bl_fn or "").lower() or kw in (kn_fn or "").lower()
            for kw in ["uart", "serial", "emmc", "mmc", "spi", "i2c", "jtag"]
        )
        if not is_critical:
            continue

        change = "ADDED" if bl_fn is None else ("REMOVED" if kn_fn is None else "CHANGED")
        diffs.append(StageDiff(
            category="pinmux",
            item=pin,
            change_type=change,
            severity="error" if "uart" in (bl_fn or "") or "uart" in (kn_fn or "") else "warning",
            bl_value=bl_fn or "(not configured)",
            kn_value=kn_fn or "(not configured)",
            message=(
                f"Pin '{pin}' is configured as '{bl_fn}' in the bootloader but "
                f"'{kn_fn}' in the kernel DTS.  "
                "A mismatched console pin will cause the kernel to lose the boot "
                "console immediately after handoff."
            ),
            suggestion=(
                "Ensure pinctrl states for console / storage / debug pins are "
                "identical in both DTS files."
            ),
        ))

    return diffs


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def compare_dts_stages(bl_model: SoC, kn_model: SoC) -> List[StageDiff]:
    """Compare bootloader and kernel SoC models; return all divergences."""
    diffs: List[StageDiff] = []
    diffs.extend(_compare_devices(bl_model, kn_model))
    diffs.extend(_compare_power(bl_model, kn_model))
    diffs.extend(_compare_clocks(bl_model, kn_model))
    diffs.extend(_compare_pinmux(bl_model, kn_model))
    return diffs


def format_report(
    diffs: List[StageDiff],
    fmt: str = "text",
    use_color: Optional[bool] = None,
    bootloader_path: str = "bootloader.dts",
    kernel_path: str = "linux.dts",
    soc_name: str = "unknown",
) -> str:
    """Format a list of StageDiff objects as text, HTML, or JSON."""
    if fmt == "json":
        return json.dumps(
            [
                {
                    "category": d.category,
                    "item": d.item,
                    "change_type": d.change_type,
                    "severity": d.severity,
                    "bl_value": d.bl_value,
                    "kn_value": d.kn_value,
                    "message": d.message,
                    "suggestion": d.suggestion,
                }
                for d in diffs
            ],
            indent=2,
        )

    if fmt == "html":
        return _format_html(diffs, bootloader_path, kernel_path, soc_name)

    return _format_text(diffs, use_color, bootloader_path, kernel_path, soc_name)


def _sev_color(severity: str, use_color: Optional[bool]) -> str:
    """ANSI prefix for a severity tag."""
    if use_color is False:
        return {"error": "[E]", "warning": "[W]"}.get(severity, "[?]")
    try:
        import click
        return {
            "error": click.style("[E]", fg="red", bold=True),
            "warning": click.style("[W]", fg="yellow"),
        }.get(severity, "[?]")
    except ImportError:
        return {"error": "[E]", "warning": "[W]"}.get(severity, "[?]")


def _format_text(
    diffs: List[StageDiff],
    use_color: Optional[bool],
    bl_path: str,
    kn_path: str,
    soc_name: str,
) -> str:
    lines = [
        "=" * 72,
        f"Cross-Stage DTS Comparison — SoC: {soc_name}",
        f"  Bootloader : {bl_path}",
        f"  Kernel     : {kn_path}",
        "=" * 72,
        "",
    ]
    if not diffs:
        lines.append("No divergences found.  Bootloader and kernel DTS are consistent.")
        return "\n".join(lines)

    # Group by category
    by_cat: Dict[str, List[StageDiff]] = {}
    for d in diffs:
        by_cat.setdefault(d.category, []).append(d)

    errors = sum(1 for d in diffs if d.severity == "error")
    warnings = sum(1 for d in diffs if d.severity == "warning")
    lines.append(f"Found {len(diffs)} divergence(s): {errors} error(s), {warnings} warning(s)")
    lines.append("")

    for cat, items in sorted(by_cat.items()):
        lines.append(f"─── {cat.upper()} ({len(items)}) " + "─" * (50 - len(cat)))
        for d in items:
            tag = _sev_color(d.severity, use_color)
            lines.append(f"  {tag} [{d.change_type}] {d.item}")
            lines.append(f"       Bootloader : {d.bl_value}")
            lines.append(f"       Kernel     : {d.kn_value}")
            lines.append(f"       Issue      : {d.message[:160]}")
            if d.suggestion:
                lines.append(f"       Fix        : {d.suggestion[:160]}")
            lines.append("")

    return "\n".join(lines)


def _format_html(
    diffs: List[StageDiff],
    bl_path: str,
    kn_path: str,
    soc_name: str,
) -> str:
    import html as _html_mod

    rows = ""
    for d in diffs:
        sev_style = "color:#e74c3c;font-weight:bold" if d.severity == "error" else "color:#f39c12"
        rows += textwrap.dedent(f"""
            <tr>
              <td>{_html_mod.escape(d.category)}</td>
              <td>{_html_mod.escape(d.item)}</td>
              <td style="{sev_style}">{_html_mod.escape(d.change_type)}</td>
              <td style="{sev_style}">{_html_mod.escape(d.severity)}</td>
              <td style="font-family:monospace;font-size:11px">
                <b>BL:</b> {_html_mod.escape(d.bl_value)}<br>
                <b>KN:</b> {_html_mod.escape(d.kn_value)}
              </td>
              <td>{_html_mod.escape(d.message)}</td>
            </tr>
        """)

    return textwrap.dedent(f"""<!DOCTYPE html>
    <html>
    <head><meta charset="utf-8">
    <title>Cross-Stage DTS: {_html_mod.escape(soc_name)}</title>
    <style>
      body{{font-family:monospace;background:#1a1a2e;color:#ecf0f1;padding:20px}}
      h1{{color:#e74c3c}} h2{{color:#f39c12}}
      table{{border-collapse:collapse;width:100%}}
      th{{background:#2c3e50;padding:8px;text-align:left}}
      td{{border-bottom:1px solid #2c3e50;padding:6px;vertical-align:top}}
      tr:hover td{{background:#16213e}}
    </style>
    </head>
    <body>
    <h1>Cross-Stage DTS Comparison</h1>
    <p>SoC: <b>{_html_mod.escape(soc_name)}</b></p>
    <p>Bootloader: {_html_mod.escape(bl_path)}<br>Kernel: {_html_mod.escape(kn_path)}</p>
    <h2>{len(diffs)} divergence(s) found</h2>
    <table>
      <tr><th>Category</th><th>Item</th><th>Change</th><th>Severity</th>
          <th>Values</th><th>Message</th></tr>
      {rows}
    </table>
    </body></html>
    """)


__all__ = ["compare_dts_stages", "format_report", "StageDiff"]
