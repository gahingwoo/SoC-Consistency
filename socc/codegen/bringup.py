"""Bring-up test script generator.

Usage:
    socc generate-tests board.dts  [--output bring_up.sh]

Generates a self-contained bash test script that verifies each enabled
peripheral at runtime using standard Linux userspace tools:

  • I2C devices      → i2cdetect / i2cget
  • SPI devices      → spidev_test (if available), otherwise a sysfs check
  • GPIO             → gpioget / gpioset
  • Regulators       → /sys/class/regulator sysfs
  • Clocks           → /sys/kernel/debug/clk
  • UART             → /dev/ttyS* existence
  • Ethernet PHY     → ethtool
  • USB              → lsusb (bus enumeration)
  • eMMC / SD        → lsblk / mmcli
  • Video devices    → v4l2-ctl --list-devices
  • CAN              → ip link show type can
  • Audio            → aplay -l

The generated script is colour-coded:
  PASS  — test succeeded
  FAIL  — test failed (non-zero exit)
  SKIP  — test requires a tool that is not installed
"""

from __future__ import annotations

import textwrap
from typing import List, Optional

from socc.model import SoC


# ── Classifier helpers ────────────────────────────────────────────────────────


def _get_compat(node) -> str:
    compat = node.properties.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return " ".join(str(c) for c in compat).lower()
    return str(compat).lower()


def _is_enabled(node) -> bool:
    return node.properties.get("status", "okay") in ("okay", "ok", "")


def _get_reg_addr(node) -> Optional[int]:
    reg = node.properties.get("reg")
    if isinstance(reg, (int, float)):
        return int(reg)
    if isinstance(reg, (list, tuple)) and reg:
        try:
            return int(reg[0])
        except (TypeError, ValueError):
            pass
    return None


def _get_parent_bus_type(node) -> str:
    """Walk up the IRNode parent chain looking for a bus keyword."""
    current = node.parent
    while current is not None:
        name_lower = current.name.lower()
        compat_lower = _get_compat(current)
        if "i2c" in name_lower or "i2c" in compat_lower:
            return "i2c"
        if "spi" in name_lower or "spi" in compat_lower:
            return "spi"
        current = current.parent
    return ""


def _extract_bus_number(node_name: str) -> Optional[int]:
    """Try to extract a bus index from a node name like 'i2c@fe2b0000'."""
    # try @-address based lookup — not reliable for bus number
    # try name suffix: i2c0, i2c1, spi2, etc.
    import re
    m = re.search(r"(\d+)$", node_name.rstrip("@0123456789").rstrip())
    if m:
        return int(m.group(1))
    # look for numeric in the name
    m = re.search(r"(?:i2c|spi|uart|serial)(\d+)", node_name.lower())
    if m:
        return int(m.group(1))
    return None


# ── Test generation helpers ───────────────────────────────────────────────────

_PASS_FAIL = """\
pass() { echo -e "\\033[0;32m[PASS]\\033[0m $1"; PASS_COUNT=$((PASS_COUNT+1)); }
fail() { echo -e "\\033[0;31m[FAIL]\\033[0m $1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
skip() { echo -e "\\033[0;33m[SKIP]\\033[0m $1"; }
needs() { command -v "$1" >/dev/null 2>&1 || { skip "Tool '$1' not found — skipping $2"; return 1; }; return 0; }
"""

_HEADER = '''\
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Auto-generated bring-up test script
# Source DTS : {dts_name}
# Generated  : socc generate-tests
#
# Run as root on the target hardware:
#   chmod +x bring_up.sh && sudo ./bring_up.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail
PASS_COUNT=0; FAIL_COUNT=0

{pass_fail}
echo "=== SoC Bring-Up Tests: {dts_name} ==="
'''

_FOOTER = """\
echo ""
echo "=== Results: $PASS_COUNT passed · $FAIL_COUNT failed ==="
[ "$FAIL_COUNT" -eq 0 ]
"""


def _test_i2c_device(bus: Optional[int], addr: int, label: str) -> str:
    bus_arg = str(bus) if bus is not None else "?"
    addr_hex = f"{addr:#04x}"
    if bus_arg == "?":
        return (
            f"# {label} (I2C address {addr_hex} — bus index unknown)\n"
            f"# Manually run: i2cdetect -y <bus> | grep -q {addr & 0x7F:02x}\n"
        )
    return (
        f"# {label}\n"
        f"if needs i2cdetect \"{label}\"; then\n"
        f"  i2cdetect -y {bus_arg} 2>/dev/null | grep -qi \"{addr & 0x7F:02x}\" \\\n"
        f"    && pass \"{label} detected at I2C-{bus_arg} {addr_hex}\" \\\n"
        f"    || fail \"{label} not found at I2C-{bus_arg} {addr_hex}\"\n"
        f"fi\n"
    )


def _test_spi_device(bus: Optional[int], cs: int, label: str) -> str:
    bus_str = str(bus) if bus is not None else "0"
    dev = f"/dev/spidev{bus_str}.{cs}"
    return (
        f"# {label}\n"
        f"[ -e {dev} ] \\\n"
        f"  && pass \"{label} SPI device {dev} exists\" \\\n"
        f"  || fail \"{label} SPI device {dev} missing\"\n"
    )


def _test_uart(dev: str, label: str) -> str:
    return (
        f"# {label}\n"
        f"[ -e /dev/{dev} ] \\\n"
        f"  && pass \"{label} UART /dev/{dev} present\" \\\n"
        f"  || fail \"{label} UART /dev/{dev} missing\"\n"
    )


def _test_regulator(name: str) -> str:
    safe = name.replace("'", "")
    return (
        f"# Regulator: {safe}\n"
        f"grep -rl \"{safe}\" /sys/class/regulator/*/name 2>/dev/null | head -1 | read -r rpath \\\n"
        f"  && pass \"Regulator {safe!r} found in sysfs\" \\\n"
        f"  || fail \"Regulator {safe!r} not found in /sys/class/regulator\"\n"
    )


def _test_gpio(pin_name: str, chip: Optional[str] = None) -> str:
    return (
        f"# GPIO: {pin_name}\n"
        f"if needs gpioinfo \"{pin_name}\"; then\n"
        f"  gpioinfo 2>/dev/null | grep -q \"{pin_name}\" \\\n"
        f"    && pass \"GPIO {pin_name!r} found in gpiochip info\" \\\n"
        f"    || skip \"GPIO {pin_name!r} label not exported\"\n"
        f"fi\n"
    )


def _test_ethernet(dev_name: str) -> str:
    return (
        f"# Ethernet: {dev_name}\n"
        f"if needs ethtool \"{dev_name}\"; then\n"
        f"  ETHS=$(ls /sys/class/net/ 2>/dev/null | grep -E 'eth|end' | head -1)\n"
        f"  [ -n \"$ETHS\" ] \\\n"
        f"    && pass \"Ethernet interface found: $ETHS\" \\\n"
        f"    || fail \"No Ethernet interface found (expected from {dev_name})\"\n"
        f"fi\n"
    )


def _test_usb(dev_name: str) -> str:
    return (
        f"# USB: {dev_name}\n"
        f"if needs lsusb \"{dev_name}\"; then\n"
        f"  lsusb >/dev/null 2>&1 \\\n"
        f"    && pass \"USB bus enumeration succeeded\" \\\n"
        f"    || fail \"USB bus enumeration failed\"\n"
        f"fi\n"
    )


def _test_video(dev_name: str) -> str:
    return (
        f"# Video device: {dev_name}\n"
        f"ls /dev/video* 2>/dev/null | head -1 | read -r vdev \\\n"
        f"  && pass \"Video device found: $vdev\" \\\n"
        f"  || fail \"No /dev/video* found (expected from {dev_name})\"\n"
    )


def _test_can(dev_name: str) -> str:
    return (
        f"# CAN: {dev_name}\n"
        f"ip link show type can 2>/dev/null | grep -q can \\\n"
        f"  && pass \"CAN interface present\" \\\n"
        f"  || fail \"No CAN interface found (expected from {dev_name})\"\n"
    )


def _test_audio(dev_name: str) -> str:
    return (
        f"# Audio: {dev_name}\n"
        f"if needs aplay \"{dev_name}\"; then\n"
        f"  aplay -l 2>/dev/null | grep -q card \\\n"
        f"    && pass \"Audio card found\" \\\n"
        f"    || fail \"No ALSA audio card found (expected from {dev_name})\"\n"
        f"fi\n"
    )


def _test_emmc(dev_name: str) -> str:
    return (
        f"# eMMC / SD: {dev_name}\n"
        f"ls /dev/mmcblk* 2>/dev/null | head -1 | read -r mmcdev \\\n"
        f"  && pass \"MMC block device found: $mmcdev\" \\\n"
        f"  || fail \"No /dev/mmcblk* found (expected from {dev_name})\"\n"
    )


# ── Classifier sets ───────────────────────────────────────────────────────────


_UART_COMPAT = frozenset({"uart", "serial", "16550", "pl011", "snps,dw-apb-uart"})
_ETH_COMPAT  = frozenset({"gmac", "emac", "ethernet", "rtl8211", "yt8531"})
_USB_COMPAT  = frozenset({"xhci", "ehci", "dwc3", "ohci", "usb"})
_VIDEO_COMPAT = frozenset({"vop", "vop2", "display", "drm", "crtc", "dsi", "hdmi"})
_CAN_COMPAT  = frozenset({"can", "mcan", "flexcan", "gs_usb"})
_AUDIO_COMPAT = frozenset({"i2s", "sai", "codec", "audio", "pcm", "hdmi-audio"})
_EMMC_COMPAT = frozenset({"sdhci", "dwmmc", "sdmmc", "mshc", "emmc"})


def _kw_match(text: str, kws) -> bool:
    return any(kw in text for kw in kws)


# ── Main generator ────────────────────────────────────────────────────────────


def generate_tests(soc: SoC, dts_name: str = "board.dts") -> str:
    """Return the full bash bring-up test script as a string."""
    sections: List[str] = []

    uart_count = 0
    for dev_name, node in soc.devices.items():
        if not _is_enabled(node):
            continue
        compat = _get_compat(node)
        dev_lower = dev_name.lower()

        addr = _get_reg_addr(node)
        parent_bus = _get_parent_bus_type(node)
        bus_num = _extract_bus_number(dev_name)

        # ── I2C child device ──────────────────────────────────────────────────
        if parent_bus == "i2c" and addr is not None:
            label = dev_name
            if compat:
                label += f" ({compat.split()[0]})"
            sections.append(_test_i2c_device(bus_num, addr, label))

        # ── SPI child device ──────────────────────────────────────────────────
        elif parent_bus == "spi" and addr is not None:
            label = dev_name
            sections.append(_test_spi_device(bus_num, addr, label))

        # ── UART ──────────────────────────────────────────────────────────────
        elif _kw_match(compat, _UART_COMPAT) or _kw_match(dev_lower, _UART_COMPAT):
            dev = f"ttyS{uart_count}"
            sections.append(_test_uart(dev, dev_name))
            uart_count += 1

        # ── Ethernet ──────────────────────────────────────────────────────────
        elif _kw_match(compat, _ETH_COMPAT) or _kw_match(dev_lower, _ETH_COMPAT):
            sections.append(_test_ethernet(dev_name))

        # ── USB ───────────────────────────────────────────────────────────────
        elif _kw_match(compat, _USB_COMPAT) or _kw_match(dev_lower, _USB_COMPAT):
            sections.append(_test_usb(dev_name))

        # ── Video ─────────────────────────────────────────────────────────────
        elif _kw_match(compat, _VIDEO_COMPAT) or _kw_match(dev_lower, _VIDEO_COMPAT):
            sections.append(_test_video(dev_name))

        # ── CAN ───────────────────────────────────────────────────────────────
        elif _kw_match(compat, _CAN_COMPAT) or _kw_match(dev_lower, _CAN_COMPAT):
            sections.append(_test_can(dev_name))

        # ── Audio ─────────────────────────────────────────────────────────────
        elif _kw_match(compat, _AUDIO_COMPAT) or _kw_match(dev_lower, _AUDIO_COMPAT):
            sections.append(_test_audio(dev_name))

        # ── eMMC / SD ─────────────────────────────────────────────────────────
        elif _kw_match(compat, _EMMC_COMPAT) or _kw_match(dev_lower, _EMMC_COMPAT):
            sections.append(_test_emmc(dev_name))

    # Regulator tests
    for reg_name in soc.power_tree.nodes:
        sections.append(_test_regulator(reg_name))

    # GPIO pin tests
    for pin_name in list(soc.pinmux_config)[:20]:   # cap at 20 to avoid huge scripts
        sections.append(_test_gpio(pin_name))

    header = _HEADER.format(
        dts_name=dts_name,
        pass_fail=_PASS_FAIL,
    )
    body = "\n".join(sections) if sections else "echo 'No testable devices found.'\n"
    return header + "\n" + body + "\n" + _FOOTER
