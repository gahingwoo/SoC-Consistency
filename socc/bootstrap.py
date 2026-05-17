"""socc bootstrap — generate a SoC YAML constraint stub from Linux mainline .dtsi files.

Scans a directory of .dtsi files (e.g. arch/arm64/boot/dts/<vendor>/),
extracts GPIO controllers, clock providers and interrupt controllers,
and writes a minimal data/soc/<vendor>/<soc>.yaml that socc can use
immediately for basic checks.

Usage (CLI wired in socc/commands/core.py):
    socc bootstrap --from-mainline ./linux/arch/arm64/boot/dts/rockchip/ --soc rk3588
    socc bootstrap --from-mainline ./linux/arch/arm/boot/dts/nxp/  # auto-detect SoC names
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── regex patterns ────────────────────────────────────────────────────────────

_RE_NODE_OPEN  = re.compile(r'^[\s\t]*(\w[\w@-]*)\s*\{', re.MULTILINE)
_RE_COMPATIBLE = re.compile(r'compatible\s*=\s*"([^"]+)"')
_RE_GPIO_CELLS = re.compile(r'#gpio-cells\s*=\s*<\s*(\d+)\s*>')
_RE_GPIO_NGPIO = re.compile(r'ngpios\s*=\s*<\s*(\d+)\s*>')
_RE_CLK_CELLS  = re.compile(r'#clock-cells\s*=\s*<\s*(\d+)\s*>')
_RE_IRQ_CELLS  = re.compile(r'#interrupt-cells\s*=\s*<\s*(\d+)\s*>')
_RE_ADDR_CELLS = re.compile(r'#address-cells\s*=\s*<\s*(\d+)\s*>')
_RE_SIZE_CELLS = re.compile(r'#size-cells\s*=\s*<\s*(\d+)\s*>')
_RE_REG        = re.compile(r'\breg\s*=\s*<\s*(0x[0-9a-fA-F]+)')
_RE_NODE_ADDR  = re.compile(r'@([0-9a-fA-F]+)')
_RE_COMMENT    = re.compile(r'/\*.*?\*/|//[^\n]*', re.DOTALL)
_RE_INCLUDE    = re.compile(r'#include\s+"([^"]+)"')
_RE_CLK_FREQ   = re.compile(r'clock-frequency\s*=\s*<\s*(0x[0-9a-fA-F]+|\d+)\s*>')


def _strip_comments(text: str) -> str:
    return _RE_COMMENT.sub(' ', text)


def _collect_dtsi_files(directory: Path, soc_hint: Optional[str] = None) -> List[Path]:
    """Return all .dtsi (and optionally matching .dts) files in *directory*."""
    files: List[Path] = []
    for ext in ("*.dtsi", "*.dts"):
        files.extend(directory.rglob(ext))
    if soc_hint:
        # prefer files whose name starts with the SoC hint
        hint_low = soc_hint.lower().replace("-", "").replace("_", "")
        scored = []
        for f in files:
            stem = f.stem.lower().replace("-", "").replace("_", "")
            priority = 0 if stem.startswith(hint_low) else 1
            scored.append((priority, f))
        scored.sort(key=lambda t: (t[0], t[1].name))
        files = [f for _, f in scored]
    return files


def _parse_dtsi_blocks(text: str) -> List[Dict[str, Any]]:
    """Very lightweight structural parse: find node blocks and their properties."""
    clean = _strip_comments(text)
    blocks: List[Dict[str, Any]] = []
    # Find all top-level and nested { ... } blocks (up to depth 3)
    # We walk character-by-character to track depth
    depth = 0
    block_start = -1
    block_name = ""
    i = 0
    lines = clean.split('\n')
    flat = clean
    pos = 0
    while pos < len(flat):
        ch = flat[pos]
        if ch == '{':
            if depth == 0:
                # look backward for node name
                pre = flat[max(0, pos-80):pos]
                m = re.search(r'(\w[\w@,-]*)\s*$', pre)
                block_name = m.group(1) if m else ""
                block_start = pos
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and block_start >= 0:
                body = flat[block_start+1:pos]
                blocks.append({"name": block_name, "body": body})
                block_start = -1
        pos += 1
    return blocks


def _extract_gpio_banks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract GPIO controller descriptors from parsed blocks."""
    banks = []
    for blk in blocks:
        body = blk["body"]
        if '#gpio-cells' not in body:
            continue
        compat_m = _RE_COMPATIBLE.search(body)
        ngpio_m  = _RE_GPIO_NGPIO.search(body)
        n = int(ngpio_m.group(1)) if ngpio_m else 32  # default 32 pins
        bank: Dict[str, Any] = {
            "name":       blk["name"],
            "compatible": compat_m.group(1) if compat_m else "",
            "pins":       n,
        }
        addr_m = _RE_NODE_ADDR.search(blk["name"])
        if addr_m:
            bank["base_address"] = "0x" + addr_m.group(1).upper()
        banks.append(bank)
    return banks


def _extract_clocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract clock provider descriptors."""
    clocks = []
    for blk in blocks:
        body = blk["body"]
        if '#clock-cells' not in body:
            continue
        compat_m = _RE_COMPATIBLE.search(body)
        freq_m   = _RE_CLK_FREQ.search(body)
        entry: Dict[str, Any] = {
            "name":       blk["name"],
            "compatible": compat_m.group(1) if compat_m else "",
        }
        if freq_m:
            raw = freq_m.group(1)
            entry["frequency_hz"] = int(raw, 16) if raw.startswith("0x") else int(raw)
        clocks.append(entry)
    return clocks


def _extract_interrupts(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract interrupt controller descriptors."""
    irqs = []
    for blk in blocks:
        body = blk["body"]
        if '#interrupt-cells' not in body:
            continue
        compat_m = _RE_COMPATIBLE.search(body)
        irqs.append({
            "name":       blk["name"],
            "compatible": compat_m.group(1) if compat_m else "",
        })
    return irqs


def bootstrap_from_directory(
    directory: str | Path,
    soc_name: Optional[str],
    output_dir: Optional[str | Path] = None,
    *,
    verbose: bool = False,
) -> Path:
    """Parse *.dtsi files in *directory* and write a SoC YAML stub.

    Returns the path to the written YAML file.
    Raises :exc:`ValueError` if no useful data is found.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    files = _collect_dtsi_files(directory, soc_hint=soc_name)
    if not files:
        raise ValueError(f"No .dtsi / .dts files found in {directory}")

    if verbose:
        print(f"[bootstrap] Scanning {len(files)} file(s) in {directory} …")

    all_blocks: List[Dict[str, Any]] = []
    for f in files[:20]:   # cap at 20 files to stay fast
        try:
            text = f.read_text(errors="replace")
            all_blocks.extend(_parse_dtsi_blocks(text))
        except OSError:
            pass

    gpio_banks   = _extract_gpio_banks(all_blocks)
    clock_providers = _extract_clocks(all_blocks)
    irq_controllers = _extract_interrupts(all_blocks)

    if not gpio_banks and not clock_providers:
        raise ValueError(
            "Could not extract any GPIO or clock information from the .dtsi files.\n"
            "Make sure the directory contains chip-level .dtsi files (not only board files)."
        )

    # ── auto-detect SoC name from directory or first matching filename ────────
    if not soc_name:
        candidate = directory.name.lower()
        if candidate not in ("dts", "boot", "arch"):
            soc_name = candidate
        else:
            for f in files[:5]:
                stem = f.stem.replace("-", "_")
                parts = stem.split("_")
                if len(parts) >= 2:
                    soc_name = parts[0]
                    break
        soc_name = soc_name or "unknown-soc"

    # ── determine vendor from SoC name prefix ────────────────────────────────
    vendor = _guess_vendor(soc_name)

    # ── build YAML content (manual, no pyyaml dep for indentation control) ────
    lines = [
        f"# Auto-generated by 'socc bootstrap'",
        f"# Source: {directory}",
        f"# Edit this file to add signal-level constraints.",
        f"",
        f"name: {soc_name}",
        f"vendor: {vendor}",
        f"family: {soc_name[:4] if len(soc_name) >= 4 else soc_name}",
        f"",
        f"# ── GPIO ──────────────────────────────────────────────",
        f"gpio:",
    ]
    for bank in gpio_banks:
        lines.append(f"  - name: {bank['name']}")
        if bank.get("base_address"):
            lines.append(f"    base: {bank['base_address']}")
        lines.append(f"    pins: {bank['pins']}")
        if bank.get("compatible"):
            lines.append(f"    compatible: \"{bank['compatible']}\"")

    lines += ["", "# ── Clocks ────────────────────────────────────────────",
              "clock_providers:"]
    for ck in clock_providers[:10]:
        lines.append(f"  - name: {ck['name']}")
        if ck.get("compatible"):
            lines.append(f"    compatible: \"{ck['compatible']}\"")
        if ck.get("frequency_hz"):
            lines.append(f"    frequency_hz: {ck['frequency_hz']}")

    lines += ["", "# ── Interrupt controllers ────────────────────────────",
              "interrupt_controllers:"]
    for ic in irq_controllers[:6]:
        lines.append(f"  - name: {ic['name']}")
        if ic.get("compatible"):
            lines.append(f"    compatible: \"{ic['compatible']}\"")

    lines += [
        "",
        "# ── Constraints (fill in from datasheet) ─────────────",
        "constraints:",
        "  max_gpio_index: {}".format(max((b["pins"] - 1) for b in gpio_banks) if gpio_banks else 31),
        "  # max_clock_mhz: 1800",
        "  # max_memory_mb: 8192",
    ]

    yaml_text = "\n".join(lines) + "\n"

    # ── write output ──────────────────────────────────────────────────────────
    if output_dir is None:
        out_root = Path(__file__).parent.parent / "data" / "soc" / vendor
    else:
        out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    out_path = out_root / f"{soc_name}.yaml"
    out_path.write_text(yaml_text)

    if verbose:
        print(f"[bootstrap] Found: {len(gpio_banks)} GPIO bank(s), "
              f"{len(clock_providers)} clock provider(s), "
              f"{len(irq_controllers)} interrupt controller(s)")

    return out_path


def _guess_vendor(soc_name: str) -> str:
    """Guess vendor string from SoC name prefix."""
    low = soc_name.lower()
    if low.startswith("rk"):
        return "rockchip"
    if low.startswith("imx") or low.startswith("mx"):
        return "nxp"
    if low.startswith("s5p") or low.startswith("exynos"):
        return "samsung"
    if low.startswith("sun") or low.startswith("a1") or low.startswith("h6") or low.startswith("h3"):
        return "allwinner"
    if low.startswith("am") or low.startswith("bcm") or low.startswith("sdm") or low.startswith("sm"):
        return "qualcomm"
    if low.startswith("meson") or low.startswith("g12"):
        return "amlogic"
    if low.startswith("mt") or low.startswith("mt8"):
        return "mediatek"
    if low.startswith("tegra") or low.startswith("t19") or low.startswith("t23"):
        return "nvidia"
    return "generic"
