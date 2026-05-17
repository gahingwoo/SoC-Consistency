"""Cross-platform DTS migration assistant.

Usage:
    socc migrate --from rk3399_board.dts --to rk3588.dtsi [--soc rk3588]

Reads an old board DTS, extracts the peripheral devices defined there
(I2C sensors, SPI flash, regulators, etc.), and attempts to map each one
onto the new target SoC DTS.

The output is a ``migration_report`` detailing:

  - ✅ AUTO-MAPPED   : peripheral found on a compatible bus/address in new SoC
  - ⚠  NEEDS REVIEW : peripheral exists in new DTS but bus/address differs
  - ❌ UNMAPPABLE    : no suitable bus or pin available in the new SoC
  - ℹ  INFO         : informational notes (driver rename, compatible update)

The tool also emits a partial DTS snippet for each migrated device so the
engineer can copy it directly into the new board file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from socc.model import SoC


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class MigrationEntry:
    """Result of migrating one peripheral to the new SoC."""
    status: str             # "AUTO_MAPPED" | "NEEDS_REVIEW" | "UNMAPPABLE" | "INFO"
    severity: str           # "ok" | "warning" | "error" | "info"
    dev_name: str           # name in old DTS
    compatible: str         # compatible string(s)
    old_bus: str            # "I2C-1", "SPI-0", "unknown" …
    old_address: Optional[int]
    new_bus: str            # bus in new SoC (or "" if unmappable)
    new_address: Optional[int]
    message: str
    suggestion: str
    dts_snippet: str        # partial DTS text to paste into new board file


@dataclass
class MigrationReport:
    old_dts: str
    new_base: str
    entries: List[MigrationEntry] = field(default_factory=list)

    @property
    def auto_mapped(self) -> List[MigrationEntry]:
        return [e for e in self.entries if e.status == "AUTO_MAPPED"]

    @property
    def needs_review(self) -> List[MigrationEntry]:
        return [e for e in self.entries if e.status == "NEEDS_REVIEW"]

    @property
    def unmappable(self) -> List[MigrationEntry]:
        return [e for e in self.entries if e.status == "UNMAPPABLE"]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_compat(node) -> str:
    c = node.properties.get("compatible", "")
    if isinstance(c, (list, tuple)):
        return ", ".join(str(x) for x in c)
    return str(c)


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


def _parent_bus_label(node) -> str:
    """Walk up parent chain and return 'I2C-N' / 'SPI-N' / 'unknown'."""
    import re
    current = node.parent
    depth = 0
    while current is not None and depth < 5:
        n = current.name.lower()
        if "i2c" in n:
            m = re.search(r"(\d+)", current.name)
            return f"I2C-{m.group(1) if m else '?'}"
        if "spi" in n:
            m = re.search(r"(\d+)", current.name)
            return f"SPI-{m.group(1) if m else '?'}"
        current = current.parent
        depth += 1
    return "unknown"


def _compatible_changed(old_compat: str) -> Optional[str]:
    """Return a suggested updated compatible string if the old one is deprecated."""
    RENAMES = {
        "rockchip,rk3399-i2c": "rockchip,rk3588-i2c",
        "rockchip,rk3399-spi": "rockchip,rk3588-spi",
        "rockchip,rk3399-uart": "rockchip,rk3588-uart",
        "rockchip,rk3399-gmac": "rockchip,rk3588-gmac",
        "rockchip,rk3399-pcie": "rockchip,rk3588-pcie3x4",
        "rockchip,rk3399-usb3": "rockchip,rk3588-dwc3",
        "rockchip,rk3399-saradc": "rockchip,rk3588-saradc",
        "rockchip,rk808": "rockchip,rk817",   # common PMIC upgrade
    }
    for old, new in RENAMES.items():
        if old in old_compat.lower():
            return new
    return None


def _build_dts_snippet(dev_name: str, addr: Optional[int], props: Dict[str, Any], new_bus_path: str) -> str:
    """Generate a minimal DTS snippet for the device under its new bus."""
    addr_str = f"@{addr:x}" if addr is not None else ""
    lines = [f"\t/* Auto-migrated: {dev_name} */"]
    lines.append(f"\t{dev_name.split('@')[0]}{addr_str} {{")

    for key in ("compatible", "reg", "status", "#address-cells", "#size-cells"):
        val = props.get(key)
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            val_str = " ".join(f"<{v}>" if isinstance(v, int) else f'"{v}"' for v in val)
        elif isinstance(val, int):
            val_str = f"<{val}>"
        else:
            val_str = f'"{val}"'
        lines.append(f"\t\t{key} = {val_str};")

    lines.append("\t};")
    return "\n".join(lines)


# ── Bus discovery in the new SoC ──────────────────────────────────────────────


def _find_i2c_buses(model: SoC) -> Dict[str, str]:
    """Return {dev_name: bus_label} for all enabled I2C controllers."""
    import re
    buses = {}
    for name, node in model.devices.items():
        c = _get_compat(node).lower()
        n = name.lower()
        if ("i2c" in c or "i2c" in n) and _is_enabled(node):
            m = re.search(r"(\d+)", name)
            label = f"I2C-{m.group(1) if m else '?'}"
            buses[name] = label
    return buses


def _find_spi_buses(model: SoC) -> Dict[str, str]:
    """Return {dev_name: bus_label} for all enabled SPI controllers."""
    import re
    buses = {}
    for name, node in model.devices.items():
        c = _get_compat(node).lower()
        n = name.lower()
        if ("spi" in c or "spi" in n) and _is_enabled(node) and "gpio" not in n:
            m = re.search(r"(\d+)", name)
            label = f"SPI-{m.group(1) if m else '?'}"
            buses[name] = label
    return buses


def _new_i2c_bus_for_addr(new_model: SoC, addr: int) -> Optional[str]:
    """Find an I2C bus in *new_model* that has no device at *addr* yet."""
    buses = _find_i2c_buses(new_model)
    occupied: Dict[str, set] = {b: set() for b in buses}

    for name, node in new_model.devices.items():
        bus_label = _parent_bus_label(node)
        a = _get_reg_addr(node)
        if a is not None:
            for bus_name, label in buses.items():
                if label == bus_label:
                    occupied[bus_name].add(a)

    # First bus with a free slot
    for bus_name, label in buses.items():
        if addr not in occupied.get(bus_name, set()):
            return label
    # All buses have addr occupied — return first bus with a warning
    if buses:
        return list(buses.values())[0]
    return None


# ── Core migration logic ──────────────────────────────────────────────────────


def migrate_dts(old_model: SoC, new_model: SoC, old_dts: str = "", new_base: str = "") -> MigrationReport:
    """Compare *old_model* against *new_model* and produce migration guidance."""
    report = MigrationReport(old_dts=old_dts, new_base=new_base)

    new_i2c_buses = _find_i2c_buses(new_model)
    new_spi_buses = _find_spi_buses(new_model)

    for dev_name, old_node in old_model.devices.items():
        if not _is_enabled(old_node):
            continue

        compat   = _get_compat(old_node)
        addr     = _get_reg_addr(old_node)
        old_bus  = _parent_bus_label(old_node)

        # Skip bus controllers themselves — only migrate leaf devices
        compat_lower = compat.lower()
        if any(k in compat_lower for k in ("simple-bus", "syscon", "pinctrl", "clock-controller", "interrupt-controller")):
            continue

        # Check: does the new SoC have the same device at the same address?
        same_in_new = None
        for new_name, new_node in new_model.devices.items():
            new_addr = _get_reg_addr(new_node)
            new_compat = _get_compat(new_node).lower()
            compat_overlap = any(
                old_c.strip().lower() in new_compat
                for old_c in compat.split(",")
            )
            if compat_overlap and new_addr == addr:
                same_in_new = (new_name, new_node)
                break

        updated_compat = _compatible_changed(compat)
        snippet = _build_dts_snippet(dev_name, addr, old_node.properties, "")

        if same_in_new:
            new_name, new_node = same_in_new
            new_bus = _parent_bus_label(new_node)
            status = "AUTO_MAPPED" if new_bus == old_bus else "NEEDS_REVIEW"
            report.entries.append(MigrationEntry(
                status=status,
                severity="ok" if status == "AUTO_MAPPED" else "warning",
                dev_name=dev_name,
                compatible=compat,
                old_bus=old_bus,
                old_address=addr,
                new_bus=new_bus,
                new_address=_get_reg_addr(new_node),
                message=(
                    f"{dev_name!r} ({compat}) found in new SoC DTS as {new_name!r}."
                    + (f" Bus changed from {old_bus} to {new_bus}." if new_bus != old_bus else "")
                ),
                suggestion=(
                    (f"Update compatible to {updated_compat!r}. " if updated_compat else "")
                    + ("No structural changes needed." if status == "AUTO_MAPPED" else
                       f"Update pinctrl and bus reference from {old_bus} to {new_bus}.")
                ),
                dts_snippet=snippet,
            ))
        else:
            # Try to find a suitable bus
            bus_by_type = "unknown"
            if old_bus.startswith("I2C") and new_i2c_buses:
                bus_by_type = _new_i2c_bus_for_addr(new_model, addr or 0) or "I2C-?"
                status = "NEEDS_REVIEW"
            elif old_bus.startswith("SPI") and new_spi_buses:
                bus_by_type = list(new_spi_buses.values())[0]
                status = "NEEDS_REVIEW"
            else:
                status = "UNMAPPABLE"

            report.entries.append(MigrationEntry(
                status=status,
                severity="warning" if status == "NEEDS_REVIEW" else "error",
                dev_name=dev_name,
                compatible=compat,
                old_bus=old_bus,
                old_address=addr,
                new_bus=bus_by_type,
                new_address=addr,
                message=(
                    f"{dev_name!r} ({compat}) from {old_bus} "
                    + ("not directly found in new SoC DTS." if status == "NEEDS_REVIEW"
                       else "cannot be automatically mapped to the new SoC.")
                ),
                suggestion=(
                    (f"Add to {bus_by_type} in the new board DTS. " if bus_by_type != "unknown" else "")
                    + (f"Update compatible to {updated_compat!r}. " if updated_compat else "")
                    + ("Verify pin availability and I2C/SPI bus assignment." if status == "NEEDS_REVIEW"
                       else "Check if this peripheral is supported on the new SoC; "
                            "consider a hardware redesign or replacement component.")
                ),
                dts_snippet=snippet,
            ))

        # Emit INFO note for compatible rename even for auto-mapped devices
        if updated_compat:
            report.entries.append(MigrationEntry(
                status="INFO",
                severity="info",
                dev_name=dev_name,
                compatible=compat,
                old_bus=old_bus,
                old_address=addr,
                new_bus="",
                new_address=None,
                message=f"Compatible string should be updated: {compat!r} → {updated_compat!r}",
                suggestion=f"Replace {compat!r} with {updated_compat!r} in the new board DTS.",
                dts_snippet="",
            ))

    return report


def render_migration_report(report: MigrationReport, use_color: bool = True) -> str:
    """Render the migration report as a human-readable string."""
    import click
    from pathlib import Path

    lines: List[str] = []
    hdr = (
        f"Migration Report  ·  "
        f"From: {Path(report.old_dts).name}  ·  "
        f"To: {Path(report.new_base).name}"
    )
    lines.append(click.style(hdr, fg="cyan", bold=True) if use_color else hdr)
    lines.append("")

    icons = {
        "AUTO_MAPPED":  "✅",
        "NEEDS_REVIEW": "⚠",
        "UNMAPPABLE":   "❌",
        "INFO":         "ℹ",
    }
    colors = {
        "AUTO_MAPPED":  "green",
        "NEEDS_REVIEW": "yellow",
        "UNMAPPABLE":   "red",
        "INFO":         "blue",
    }

    for entry in report.entries:
        icon = icons.get(entry.status, " ")
        tag = f"[{entry.status}]"
        if use_color:
            tag = click.style(tag, fg=colors.get(entry.status, "white"), bold=True)
        lines.append(f"{icon} {tag}  {entry.message}")
        if entry.suggestion:
            lines.append(f"       Fix : {entry.suggestion}")
        if entry.dts_snippet and entry.status != "INFO":
            lines.append("       DTS snippet:")
            for sl in entry.dts_snippet.splitlines():
                lines.append(f"         {sl}")
        lines.append("")

    auto   = len(report.auto_mapped)
    review = len(report.needs_review)
    unmap  = len(report.unmappable)
    summary = f"Summary: {auto} auto-mapped · {review} need review · {unmap} unmappable"
    color = "green" if not unmap and not review else ("red" if unmap else "yellow")
    lines.append(click.style(summary, fg=color, bold=True) if use_color else summary)
    return "\n".join(lines)
