"""Bill of Materials (BOM) vs DTS cross-reference auditor.

Usage:
    socc audit-bom board.dts hardware_bom.csv

The tool reads a factory BOM CSV (pick-and-place or purchase order format),
extracts chip part numbers and bus addresses, then cross-references them
against the ``compatible`` strings and ``reg`` addresses in the device tree.

Mismatches are reported as violations — e.g., the BOM lists an INA219 at
I2C address 0x40 but the DTS expects a MAX1617 at the same address.

── BOM CSV format ────────────────────────────────────────────────────────────
Supported column names (case-insensitive, any subset is accepted):
  RefDes | Ref       — component designator (U14, R12 …)
  PartNumber | Part  — manufacturer part number  (INA219AIDR, MAX1617AEE …)
  Manufacturer | Mfr — manufacturer name
  Description | Desc — free-text description
  Interface | Bus    — bus type keyword (I2C, SPI, UART, …)
  BusNumber | BusNum — integer bus index (1 for I2C1, 2 for SPI2 …)
  Address | Addr     — hex or decimal I2C/SPI slave address

Minimal supported format (just two columns):
  PartNumber, Address
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from socc.model import SoC


# ── Chip database ─────────────────────────────────────────────────────────────
# Maps normalised part-number keywords to (expected_compatible, description).
# The key is lowercased and stripped of package/temp-grade suffixes.
# Multiple compatible strings mean the part goes by different names.

@dataclass
class _ChipInfo:
    compatible: List[str]   # DTS compatible string(s)
    description: str
    interface: str          # "I2C", "SPI", "UART", "1-Wire", "Unknown"


_CHIP_DB: Dict[str, _ChipInfo] = {
    # ── Current / power monitors ──────────────────────────────────────────────
    "ina219":  _ChipInfo(["ti,ina219"],                       "Bidirectional Current/Power Monitor", "I2C"),
    "ina220":  _ChipInfo(["ti,ina220"],                       "Bidirectional Current/Power Monitor", "I2C"),
    "ina226":  _ChipInfo(["ti,ina226"],                       "High-Side / Low-Side Current Monitor", "I2C"),
    "ina231":  _ChipInfo(["ti,ina231"],                       "Current Monitor",                     "I2C"),
    "ina3221": _ChipInfo(["ti,ina3221"],                      "3-Ch Current Monitor",                "I2C"),
    "pac1934": _ChipInfo(["microchip,pac1934"],                "4-Ch Power Monitor",                  "I2C"),

    # ── Temperature sensors ────────────────────────────────────────────────────
    "max1617": _ChipInfo(["dallas,max1617", "maxim,max1617"],  "Remote Temperature Sensor",           "I2C"),
    "tmp102":  _ChipInfo(["ti,tmp102"],                        "Digital Temperature Sensor",          "I2C"),
    "tmp112":  _ChipInfo(["ti,tmp112"],                        "Digital Temperature Sensor",          "I2C"),
    "lm75":    _ChipInfo(["national,lm75", "lm75"],            "Temperature Sensor",                  "I2C"),
    "lm75a":   _ChipInfo(["national,lm75a"],                   "Temperature Sensor",                  "I2C"),
    "adt7490": _ChipInfo(["adi,adt7490"],                       "Fan Controller + Temperature",        "I2C"),
    "emc2305": _ChipInfo(["smsc,emc2305"],                     "Fan Controller",                      "I2C"),
    "nct7802": _ChipInfo(["nuvoton,nct7802"],                  "Hardware Monitor",                    "I2C"),

    # ── PMICs ─────────────────────────────────────────────────────────────────
    "rk808":   _ChipInfo(["rockchip,rk808"],                   "Rockchip PMIC",                       "I2C"),
    "rk809":   _ChipInfo(["rockchip,rk809"],                   "Rockchip PMIC",                       "I2C"),
    "rk817":   _ChipInfo(["rockchip,rk817"],                   "Rockchip PMIC",                       "I2C"),
    "rk818":   _ChipInfo(["rockchip,rk818"],                   "Rockchip PMIC",                       "I2C"),
    "axp209":  _ChipInfo(["x-powers,axp209"],                  "Allwinner AXP PMIC",                  "I2C"),
    "axp803":  _ChipInfo(["x-powers,axp803"],                  "Allwinner AXP PMIC",                  "I2C"),
    "axp813":  _ChipInfo(["x-powers,axp813"],                  "Allwinner AXP PMIC",                  "I2C"),
    "bd71847": _ChipInfo(["rohm,bd71847"],                     "ROHM PMIC",                           "I2C"),
    "pf5030":  _ChipInfo(["nxp,pf5030"],                      "NXP PMIC",                            "I2C"),
    "mp8859":  _ChipInfo(["mps,mp8859"],                       "MPS PMIC",                            "I2C"),
    "tps65132": _ChipInfo(["ti,tps65132"],                     "TI Dual Output PMIC",                 "I2C"),
    "tps65185": _ChipInfo(["ti,tps65185"],                     "TI e-Paper PMIC",                     "I2C"),

    # ── RTC ───────────────────────────────────────────────────────────────────
    "ds1307":  _ChipInfo(["dallas,ds1307"],                    "RTC",                                 "I2C"),
    "ds3231":  _ChipInfo(["dallas,ds3231"],                    "RTC with Temperature",                "I2C"),
    "pcf8563":  _ChipInfo(["nxp,pcf8563"],                    "RTC",                                 "I2C"),
    "rv3028":   _ChipInfo(["microcrystal,rv3028"],             "RTC",                                 "I2C"),
    "rv8263":   _ChipInfo(["microcrystal,rv8263"],             "RTC",                                 "I2C"),

    # ── IMU / Motion sensors ──────────────────────────────────────────────────
    "mpu6050": _ChipInfo(["invensense,mpu6050"],               "6-Axis IMU",                          "I2C"),
    "mpu9250": _ChipInfo(["invensense,mpu9250"],               "9-Axis IMU",                          "I2C"),
    "bmi160":  _ChipInfo(["bosch,bmi160"],                     "6-Axis IMU",                          "I2C"),
    "bmi280":  _ChipInfo(["bosch,bmi280"],                     "6-Axis IMU",                          "I2C"),
    "lsm6dsl": _ChipInfo(["st,lsm6dsl"],                       "6-Axis IMU",                          "I2C"),
    "icm42688": _ChipInfo(["invensense,icm42688p"],            "6-Axis IMU",                          "SPI"),

    # ── Environmental sensors ─────────────────────────────────────────────────
    "bmp280":  _ChipInfo(["bosch,bmp280"],                     "Pressure + Temperature",              "I2C"),
    "bme280":  _ChipInfo(["bosch,bme280"],                     "Humidity + Pressure + Temperature",   "I2C"),
    "bme680":  _ChipInfo(["bosch,bme680"],                     "Gas + Humidity + Pressure + Temp",    "I2C"),
    "sht30":   _ChipInfo(["sensirion,sht30"],                  "Humidity + Temperature",              "I2C"),
    "sht31":   _ChipInfo(["sensirion,sht31"],                  "Humidity + Temperature",              "I2C"),

    # ── Display / Touch ───────────────────────────────────────────────────────
    "gt911":   _ChipInfo(["goodix,gt911"],                     "Capacitive Touch Controller",         "I2C"),
    "ft5406":  _ChipInfo(["edt,edt-ft5406"],                   "Capacitive Touch Controller",         "I2C"),
    "st7789":  _ChipInfo(["sitronix,st7789v"],                 "LCD Display Driver",                  "SPI"),
    "ili9341":  _ChipInfo(["ilitek,ili9341"],                  "LCD Display Driver",                  "SPI"),

    # ── Camera sensors ────────────────────────────────────────────────────────
    "ov5640":  _ChipInfo(["ovti,ov5640"],                      "5MP MIPI Camera Sensor",              "I2C"),
    "ov8858":  _ChipInfo(["ovti,ov8858"],                      "8MP MIPI Camera Sensor",              "I2C"),
    "imx219":  _ChipInfo(["sony,imx219"],                      "8MP MIPI Camera Sensor",              "I2C"),
    "imx477":  _ChipInfo(["sony,imx477"],                      "12MP MIPI Camera Sensor",             "I2C"),

    # ── Ethernet ──────────────────────────────────────────────────────────────
    "yt8531":  _ChipInfo(["motorcomm,yt8531c"],                "Gigabit Ethernet PHY",                "MDIO"),
    "rtl8211f": _ChipInfo(["realtek,rtl8211f"],                "Gigabit Ethernet PHY",                "MDIO"),
    "ksz9031":  _ChipInfo(["micrel,ksz9031"],                  "Gigabit Ethernet PHY",                "MDIO"),

    # ── NOR Flash (SPI) ───────────────────────────────────────────────────────
    "w25q128":  _ChipInfo(["winbond,w25q128", "jedec,spi-nor"], "128Mb NOR Flash",                   "SPI"),
    "w25q256":  _ChipInfo(["winbond,w25q256", "jedec,spi-nor"], "256Mb NOR Flash",                   "SPI"),
    "gd25q128": _ChipInfo(["gigadevice,gd25q128", "jedec,spi-nor"], "128Mb NOR Flash",              "SPI"),

    # ── EEPROM ────────────────────────────────────────────────────────────────
    "at24c256": _ChipInfo(["atmel,24c256"],                    "256Kb I2C EEPROM",                    "I2C"),
    "at24c512": _ChipInfo(["atmel,24c512"],                    "512Kb I2C EEPROM",                    "I2C"),
    "m24512":   _ChipInfo(["st,m24512"],                       "512Kb I2C EEPROM",                    "I2C"),

    # ── GPIO expanders ────────────────────────────────────────────────────────
    "pca9555":  _ChipInfo(["nxp,pca9555"],                     "16-bit GPIO Expander",                "I2C"),
    "pca9554":  _ChipInfo(["nxp,pca9554"],                     "8-bit GPIO Expander",                 "I2C"),
    "pcf8574":  _ChipInfo(["nxp,pcf8574"],                     "8-bit GPIO Expander",                 "I2C"),
    "tca9555":  _ChipInfo(["ti,tca9555"],                      "16-bit GPIO Expander",                "I2C"),
}


# ── BOM data structures ───────────────────────────────────────────────────────


@dataclass
class BOMEntry:
    """One row from the BOM CSV."""
    ref_des: str
    part_number: str
    manufacturer: str
    description: str
    interface: str          # "I2C", "SPI", "UART", "Unknown"
    bus_number: Optional[int]
    address: Optional[int]  # I2C/SPI slave address (integer)
    raw_row: Dict[str, str] = field(default_factory=dict)


@dataclass
class BOMViolation:
    """A mismatch between BOM and DTS."""
    ref_des: str
    bom_part: str
    bom_description: str
    dts_node_name: str
    dts_node_path: str
    dts_compatible: str
    dts_description: str
    address: Optional[int]
    severity: str           # "CRITICAL" | "WARNING" | "INFO"
    message: str
    suggestion: str


# ── CSV parser ────────────────────────────────────────────────────────────────


def _norm_col(s: str) -> str:
    """Normalise a CSV column header to a canonical form."""
    return s.strip().lower().replace(" ", "_").replace("-", "_")


_COL_MAP: Dict[str, str] = {
    # refdes
    "refdes": "ref_des", "ref": "ref_des", "reference": "ref_des",
    "designator": "ref_des",
    # part number
    "partnumber": "part_number", "part_number": "part_number",
    "part": "part_number", "mpn": "part_number",
    # manufacturer
    "manufacturer": "manufacturer", "mfr": "manufacturer",
    "vendor": "manufacturer",
    # description
    "description": "description", "desc": "description",
    "value": "description",
    # interface / bus type
    "interface": "interface", "bus": "interface", "bustype": "interface",
    # bus number
    "busnumber": "bus_number", "bus_number": "bus_number",
    "busnum": "bus_number", "channel": "bus_number",
    # address
    "address": "address", "addr": "address",
    "i2c_address": "address", "spi_cs": "address",
}


def _parse_int(s: str) -> Optional[int]:
    """Parse '0x40', '64', '40h' etc. to int."""
    s = s.strip().lower().rstrip("h")
    if not s:
        return None
    try:
        return int(s, 0)   # handles 0x prefix automatically
    except ValueError:
        try:
            return int(s, 16)
        except ValueError:
            return None


def parse_bom_csv(path: str) -> List[BOMEntry]:
    """Parse a BOM CSV and return a list of BOMEntry objects."""
    entries: List[BOMEntry] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = {_norm_col(h): h for h in (reader.fieldnames or [])}

        for row in reader:
            mapped: Dict[str, str] = {}
            for norm_h, orig_h in headers.items():
                canonical = _COL_MAP.get(norm_h)
                if canonical:
                    mapped[canonical] = row[orig_h].strip()

            if not mapped.get("part_number"):
                continue  # skip empty / header rows

            entries.append(BOMEntry(
                ref_des=mapped.get("ref_des", "?"),
                part_number=mapped.get("part_number", ""),
                manufacturer=mapped.get("manufacturer", ""),
                description=mapped.get("description", ""),
                interface=mapped.get("interface", "Unknown").upper(),
                bus_number=_parse_int(mapped.get("bus_number", "")),
                address=_parse_int(mapped.get("address", "")),
                raw_row=dict(row),
            ))

    return entries


# ── Part-number normalisation ─────────────────────────────────────────────────


def _normalise_part(raw: str) -> str:
    """Strip package, grade, and revision suffixes to get the chip core name."""
    # e.g. "INA219AIDR" → "ina219", "MAX1617AEE+T" → "max1617"
    s = raw.lower()
    # remove common package/temp suffixes  (letters/digits after the base PN)
    s = re.sub(r"[abcdefghijklmnopqrstuvwxyz]+\d*[\-+_].*$", "", s)
    # remove leading/trailing whitespace and non-alphanumeric
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _lookup_chip(part_number: str) -> Optional[_ChipInfo]:
    """Return chip info for a part number, or None if not in the database."""
    norm = _normalise_part(part_number)
    if norm in _CHIP_DB:
        return _CHIP_DB[norm]
    # Try prefix match (e.g. "ina219aidr" → "ina219")
    for key, info in _CHIP_DB.items():
        if norm.startswith(key) or key.startswith(norm[:6]):
            return info
    return None


# ── DTS node lookup helpers ───────────────────────────────────────────────────


def _get_compatible_list(ir_node) -> List[str]:
    compat = ir_node.properties.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return [str(c).lower() for c in compat]
    return [str(compat).lower()] if compat else []


def _get_reg_addr(ir_node) -> Optional[int]:
    """Try to extract the I2C/SPI slave address from IRNode.properties['reg']."""
    reg = ir_node.properties.get("reg")
    if reg is None:
        return None
    if isinstance(reg, (int, float)):
        return int(reg)
    if isinstance(reg, (list, tuple)) and reg:
        try:
            return int(reg[0])
        except (TypeError, ValueError):
            pass
    if isinstance(reg, str):
        return _parse_int(reg)
    return None


# ── Core audit logic ──────────────────────────────────────────────────────────


def audit_bom(soc: SoC, bom_entries: List[BOMEntry]) -> List[BOMViolation]:
    """Cross-reference *bom_entries* against *soc* and return violations."""
    violations: List[BOMViolation] = []

    for entry in bom_entries:
        chip_info = _lookup_chip(entry.part_number)
        if chip_info is None:
            # Part not in database — emit informational note
            violations.append(BOMViolation(
                ref_des=entry.ref_des,
                bom_part=entry.part_number,
                bom_description=entry.description or "Unknown",
                dts_node_name="",
                dts_node_path="",
                dts_compatible="",
                dts_description="(not in chip database)",
                address=entry.address,
                severity="INFO",
                message=(
                    f"{entry.ref_des} ({entry.part_number}): part not found "
                    f"in the chip database — cannot verify DTS binding."
                ),
                suggestion=(
                    f"Add {entry.part_number.lower()!r} to the socc chip "
                    f"database, or manually verify the DTS compatible string."
                ),
            ))
            continue

        # Search for a matching DTS node by address
        matched_node = None
        matched_name = ""
        if entry.address is not None:
            for dev_name, ir_node in soc.devices.items():
                node_addr = _get_reg_addr(ir_node)
                if node_addr == entry.address:
                    matched_node = ir_node
                    matched_name = dev_name
                    break

        if matched_node is None:
            # No node at this address — missing DTS entry
            violations.append(BOMViolation(
                ref_des=entry.ref_des,
                bom_part=entry.part_number,
                bom_description=entry.description or chip_info.description,
                dts_node_name="",
                dts_node_path="",
                dts_compatible="",
                dts_description=chip_info.description,
                address=entry.address,
                severity="WARNING",
                message=(
                    f"{entry.ref_des} ({entry.part_number}, {chip_info.description}) "
                    f"is in the BOM at address "
                    f"{'0x{:02x}'.format(entry.address) if entry.address else '?'} "
                    f"but has no corresponding DTS device node."
                ),
                suggestion=(
                    f"Add a device node for {chip_info.compatible[0]!r} at "
                    f"address {'0x{:02x}'.format(entry.address) if entry.address else '?'} "
                    f"to the appropriate {entry.interface} bus node."
                ),
            ))
            continue

        # Node found — compare compatible strings
        node_compats = _get_compatible_list(matched_node)
        expected_compats = [c.lower() for c in chip_info.compatible]

        match_found = any(
            exp in nc or nc in exp
            for nc in node_compats
            for exp in expected_compats
        )

        if not match_found:
            dts_compat_str = node_compats[0] if node_compats else "(none)"
            violations.append(BOMViolation(
                ref_des=entry.ref_des,
                bom_part=entry.part_number,
                bom_description=chip_info.description,
                dts_node_name=matched_name,
                dts_node_path=matched_node.path,
                dts_compatible=dts_compat_str,
                dts_description=_describe_from_compat(dts_compat_str),
                address=entry.address,
                severity="CRITICAL",
                message=(
                    f"BOM/DTS mismatch at address "
                    f"{'0x{:02x}'.format(entry.address) if entry.address else '?'}: "
                    f"BOM lists {entry.ref_des} as {entry.part_number!r} "
                    f"({chip_info.description}), "
                    f"but DTS node {matched_name!r} expects "
                    f"{dts_compat_str!r} ({_describe_from_compat(dts_compat_str)})."
                ),
                suggestion=(
                    f"Either update the DTS compatible to {chip_info.compatible[0]!r} "
                    f"to match the BOM, or update the BOM to reflect what the DTS "
                    f"actually expects. Confirm the physical part was not substituted "
                    f"without updating the DTS."
                ),
            ))

    return violations


def _describe_from_compat(compat: str) -> str:
    """Return a short description for a compatible string by reverse lookup."""
    compat_lower = compat.lower()
    for info in _CHIP_DB.values():
        for c in info.compatible:
            if c.lower() == compat_lower:
                return info.description
    return "Unknown device"


def render_bom_report(
    violations: List[BOMViolation],
    bom_path: str,
    dts_path: str,
    use_color: bool = True,
) -> str:
    """Render a human-readable BOM audit report."""
    import click

    lines: List[str] = []
    hdr = f"BOM Audit Report  ·  DTS: {Path(dts_path).name}  ·  BOM: {Path(bom_path).name}"
    if use_color:
        lines.append(click.style(hdr, fg="cyan", bold=True))
    else:
        lines.append(hdr)
    lines.append("")

    criticals = [v for v in violations if v.severity == "CRITICAL"]
    warnings  = [v for v in violations if v.severity == "WARNING"]
    infos     = [v for v in violations if v.severity == "INFO"]

    def _sev_style(sev: str, text: str) -> str:
        if not use_color:
            return f"[{sev}] {text}"
        colors = {"CRITICAL": "red", "WARNING": "yellow", "INFO": "blue"}
        return click.style(f"[{sev}]", fg=colors.get(sev, "white"), bold=True) + f" {text}"

    for v in criticals + warnings:
        lines.append(_sev_style(v.severity, v.message))
        lines.append(f"         Impact     : Driver will fail to probe; the wrong chip is mounted.")
        lines.append(f"         DTS path   : {v.dts_node_path or '(missing)'}")
        lines.append(f"         DTS compat : {v.dts_compatible or '(none)'}")
        lines.append(f"         Fix        : {v.suggestion}")
        lines.append("")

    for v in infos:
        lines.append(_sev_style(v.severity, v.message))

    lines.append("")
    summary_line = (
        f"Summary: {len(criticals)} critical  ·  "
        f"{len(warnings)} warnings  ·  {len(infos)} info"
    )
    if use_color:
        color = "red" if criticals else ("yellow" if warnings else "green")
        lines.append(click.style(summary_line, fg=color, bold=True))
    else:
        lines.append(summary_line)

    return "\n".join(lines)
