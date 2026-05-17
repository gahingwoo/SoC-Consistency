"""Bare-metal C header extractor.

``socc export-headers board.dts [--output soc_hw.h]``

Scans the fully-parsed SoC model and generates a **production-quality C
header file** containing:

  • Base addresses (``#define XXX_BASE_ADDR 0xFE2B0000UL``)
  • IRQ numbers    (``#define XXX_IRQ_NUM   34``)
  • Clock IDs      (``#define CLK_XXX       12``)
  • Voltage constants for regulators
  • IO sizes / stride for peripheral register maps

The output compiles with any C89/C99/C11 toolchain and is grouped by
peripheral class so it can be dropped directly into a bare-metal or RTOS
project.

────────────────────────────────────────────────────────────────────────────
Why this matters for students
────────────────────────────────────────────────────────────────────────────
Without this tool, a student writing bare-metal code must manually look up
every register base address in a 5000-page TRM.  One transposed hex digit
crashes the system with no useful error message.  socc export-headers
eliminates this class of mistake entirely.
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from socc.model import SoC


# ── Peripheral classifier ─────────────────────────────────────────────────────

_CLASS_MAP: List[Tuple[str, List[str]]] = [
    # (class_label, [compatible / name substrings])
    ("I2C Controllers",     ["i2c", "twi"]),
    ("SPI Controllers",     ["spi", "qspi"]),
    ("UART / Serial",       ["uart", "serial", "pl011", "16550"]),
    ("GPIO Controllers",    ["gpio"]),
    ("Timers / Watchdog",   ["timer", "wdt", "watchdog"]),
    ("DMA Controllers",     ["dma", "pl330"]),
    ("USB Controllers",     ["usb", "ehci", "ohci", "xhci", "dwc3", "otg"]),
    ("Ethernet / GMAC",     ["ethernet", "gmac", "emac", "stmmac"]),
    ("eMMC / SD / NAND",    ["mmc", "emmc", "sdmmc", "sdhci", "nand", "nandc"]),
    ("PCIe Controllers",    ["pcie"]),
    ("Display / VOP",       ["vop", "display", "hdmi", "dsi", "edp", "lvds"]),
    ("Camera / ISP",        ["isp", "csi", "camera", "mipi"]),
    ("Video Codec",         ["vdec", "venc", "rkvdec", "rkvenc"]),
    ("GPU",                 ["gpu", "mali", "panfrost", "bifrost"]),
    ("NPU / AI",            ["npu", "rknn"]),
    ("CAN Bus",             ["can", "flexcan"]),
    ("Audio / SAI",         ["i2s", "sai", "audio", "pdm", "spdif"]),
    ("PWM",                 ["pwm"]),
    ("ADC",                 ["adc", "saradc"]),
    ("Power Management",    ["pmic", "rk808", "rk817", "rk806", "regulator"]),
    ("Security / Crypto",   ["crypto", "trng", "rng", "otp", "efuse", "secure", "tee"]),
    ("Interrupt Controller",["gic", "interrupt-controller"]),
    ("Clock / CRU",         ["cru", "pll", "clk"]),
    ("Memory / DRAM",       ["memory", "dram", "dfi"]),
    ("System / Misc",       []),   # catch-all
]


def _classify(dev_name: str, compat: str) -> str:
    combined = (dev_name + " " + compat).lower()
    for label, keywords in _CLASS_MAP:
        if label == "System / Misc":
            continue
        if any(k in combined for k in keywords):
            return label
    return "System / Misc"


# ── C identifier helpers ──────────────────────────────────────────────────────


def _to_c_ident(s: str) -> str:
    """Convert arbitrary string to a valid, uppercase C identifier."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_").upper()
    if s and s[0].isdigit():
        s = "DEV_" + s
    return s


def _get_compat(node) -> str:
    c = node.properties.get("compatible", "")
    if isinstance(c, (list, tuple)):
        return " ".join(str(x) for x in c).lower()
    return str(c).lower()


def _get_reg(node) -> Optional[Tuple[int, int]]:
    reg = node.properties.get("reg")
    if isinstance(reg, (list, tuple)) and len(reg) >= 2:
        try:
            return (int(reg[0]), int(reg[1]))
        except (TypeError, ValueError):
            pass
    if isinstance(reg, (int, float)):
        return (int(reg), 0)
    return None


def _get_irq(node) -> Optional[int]:
    val = node.properties.get("interrupts")
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, (list, tuple)) and val:
        # GIC interrupt cells: [type, number, flags]
        # type 0 = SPI → add 32 for Linux IRQ number
        try:
            if len(val) >= 2:
                irq_type = int(val[0])
                irq_num  = int(val[1])
                return irq_num + (32 if irq_type == 0 else 16)
            return int(val[0])
        except (TypeError, ValueError):
            pass
    return None


def _get_clock_ids(node) -> List[Tuple[str, Any]]:
    """Return [(clock_name, id), ...] from clock-names + clocks properties."""
    names = node.properties.get("clock-names", [])
    clocks = node.properties.get("clocks", [])
    if isinstance(names, str):
        names = [names]
    if not isinstance(clocks, (list, tuple)):
        clocks = [clocks]
    # Each clock ref may be a phandle (int) or [phandle, clock_id]
    results: List[Tuple[str, Any]] = []
    for i, name in enumerate(names):
        if i < len(clocks):
            ref = clocks[i]
            if isinstance(ref, (list, tuple)) and len(ref) >= 2:
                results.append((str(name), int(ref[1])))
            else:
                results.append((str(name), ref))
    return results


# ── Per-device header entry ───────────────────────────────────────────────────


def _device_defines(dev_name: str, node, prefix: str) -> List[str]:
    """Return a list of ``#define ...`` lines for this device node."""
    lines: List[str] = []
    ident = _to_c_ident(prefix)

    reg = _get_reg(node)
    if reg:
        base, size = reg
        lines.append(f"#define {ident}_BASE       0x{base:08X}UL")
        if size > 0:
            lines.append(f"#define {ident}_SIZE       0x{size:08X}UL")

    irq = _get_irq(node)
    if irq is not None:
        lines.append(f"#define {ident}_IRQ        {irq}")

    # Clock IDs
    for clk_name, clk_id in _get_clock_ids(node):
        clk_ident = _to_c_ident(f"{prefix}_{clk_name}_CLK")
        if isinstance(clk_id, int):
            lines.append(f"#define {clk_ident}  {clk_id}")

    # Clock frequency
    freq = node.properties.get("clock-frequency")
    if isinstance(freq, (int, float)) and freq > 0:
        lines.append(f"#define {ident}_CLK_HZ     {int(freq)}UL")

    return lines


# ── Regulator constants ───────────────────────────────────────────────────────


def _regulator_defines(model: SoC) -> List[str]:
    lines: List[str] = []
    for name, reg in sorted(model.power_tree.nodes.items()):
        ident = _to_c_ident(name)
        if hasattr(reg, "voltage_min") and reg.voltage_min > 0:
            lines.append(
                f"#define {ident}_MIN_UV  {int(reg.voltage_min * 1_000_000)}"
            )
        if hasattr(reg, "voltage_max") and reg.voltage_max > 0:
            lines.append(
                f"#define {ident}_MAX_UV  {int(reg.voltage_max * 1_000_000)}"
            )
    return lines


# ── Main API ──────────────────────────────────────────────────────────────────


def generate_headers(soc: SoC, dts_name: str = "board.dts",
                     include_clocks: bool = True,
                     include_regulators: bool = True) -> str:
    """Return the full C header file as a string."""

    guard = _to_c_ident(soc.name) + "_SOC_HARDWARE_H"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group devices by class
    groups: Dict[str, List[Tuple[str, Any]]] = {label: [] for label, _ in _CLASS_MAP}

    for dev_name, node in sorted(soc.devices.items()):
        compat = _get_compat(node)
        cls = _classify(dev_name, compat)
        groups[cls].append((dev_name, node))

    # Build output
    out: List[str] = [
        f"/* AUTO-GENERATED BY SoC-Consistency v{_get_version()} — DO NOT EDIT */",
        f"/* Source DTS : {dts_name}",
        f"   SoC        : {soc.name}",
        f"   Generated  : {timestamp} */",
        "",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        "",
        "/* ── Convenience macros ───────────────────────────────────────────── */",
        "#ifndef BIT",
        "#define BIT(n)  (1UL << (n))",
        "#endif",
        "#ifndef GENMASK",
        "#define GENMASK(h, l)  (((~0UL) << (l)) & (~0UL >> (sizeof(unsigned long)*8-1-(h))))",
        "#endif",
        "#ifndef MiB",
        "#define MiB  (1024UL * 1024UL)",
        "#define GiB  (1024UL * MiB)",
        "#endif",
        "",
    ]

    for label, _ in _CLASS_MAP:
        devs = groups.get(label, [])
        if not devs:
            continue

        out.append(f"/* {'─'*64} */")
        out.append(f"/* {label:^64} */")
        out.append(f"/* {'─'*64} */")
        out.append("")

        # Counter for duplicate names (e.g. I2C0, I2C1, I2C2…)
        class_counts: Dict[str, int] = {}

        for dev_name, node in devs:
            compat = _get_compat(node)
            # Generate a clean prefix: strip vendor prefix from compatible
            clean_compat = re.sub(r"^[a-z0-9_-]+,", "", compat.split()[0]) if compat else ""
            base = _to_c_ident(clean_compat or dev_name)
            count = class_counts.get(base, 0)
            class_counts[base] = count + 1
            prefix = f"{base}{count}" if count > 0 else base

            # Comment line
            out.append(f"/* {dev_name}  [{compat.split()[0] if compat else 'n/a'}] */")

            defs = _device_defines(dev_name, node, prefix)
            if defs:
                out.extend(defs)
            else:
                out.append(f"/* (no reg/irq data extracted) */")
            out.append("")

    # Power / Regulators section
    if include_regulators and model_has_regulators(soc):
        reg_defs = _regulator_defines(soc)
        if reg_defs:
            out.append(f"/* {'─'*64} */")
            out.append(f"/* {'Power Domains / Regulators':^64} */")
            out.append(f"/* {'─'*64} */")
            out.append("")
            out.extend(reg_defs)
            out.append("")

    out.append(f"#endif /* {guard} */")
    return "\n".join(out) + "\n"


def model_has_regulators(soc: SoC) -> bool:
    return bool(soc.power_tree.nodes)


def _get_version() -> str:
    try:
        from socc import __version__
        return __version__
    except Exception:
        return "?"
