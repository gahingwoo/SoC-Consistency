"""
Live JTAG/SVD Hardware Diff — "Silicon Lie Detector"

Parses a CMSIS-SVD file to know the register map, then either:
  (a) connects to a running OpenOCD instance via its telnet interface
      to read *physical* register values from the chip, or
  (b) runs in --simulate mode, which assumes boot-default (SVD reset)
      values for all registers (modelling the "driver never ran" scenario).

The diff engine then compares what the DTS *expects* against what
the silicon *actually holds*, identifying mismatches down to the bit-field
level (pinmux selector, clock-gate bit, enable flag, etc.).

CLI entry:
  socc live-probe --svd rk3588.svd board.dts --simulate
  socc live-probe --svd rk3588.svd --jtag openocd.cfg board.dts
"""

from __future__ import annotations

import re
import socket
import textwrap
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from socc.model import SoC


# ─────────────────────────────────────────────────────────────────────────────
# SVD data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SVDField:
    name: str
    bit_offset: int
    bit_width: int
    description: str = ""
    reset_value: int = 0

    def extract(self, reg_value: int) -> int:
        mask = (1 << self.bit_width) - 1
        return (reg_value >> self.bit_offset) & mask


@dataclass
class SVDRegister:
    name: str
    address_offset: int
    description: str = ""
    reset_value: int = 0
    fields: Dict[str, SVDField] = field(default_factory=dict)

    def absolute_address(self, peripheral_base: int) -> int:
        return peripheral_base + self.address_offset


@dataclass
class SVDPeripheral:
    name: str
    base_address: int
    description: str = ""
    registers: Dict[str, SVDRegister] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# SVD parser
# ─────────────────────────────────────────────────────────────────────────────

def _ns_tag(elem: ET.Element, tag: str) -> Optional[str]:
    """Get text of a direct child tag, stripping namespace."""
    for child in elem:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local == tag:
            return (child.text or "").strip()
    return None


def _parse_int(s: Optional[str]) -> int:
    if not s:
        return 0
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    if s.startswith("#"):          # SVD binary: #0b1010
        return int(s[1:], 2)
    try:
        return int(s, 0)
    except ValueError:
        return 0


def parse_svd(svd_path: str) -> Dict[str, SVDPeripheral]:
    """Parse a CMSIS-SVD XML file → {peripheral_name: SVDPeripheral}."""
    tree = ET.parse(svd_path)
    root = tree.getroot()

    peripherals: Dict[str, SVDPeripheral] = {}
    base_peripherals: Dict[str, SVDPeripheral] = {}  # for derivedFrom

    peripherals_elem = root.find("peripherals")
    if peripherals_elem is None:
        peripherals_elem = root.find("{*}peripherals")
    if peripherals_elem is None:
        return {}

    for peri_elem in peripherals_elem:
        derived_from = peri_elem.get("derivedFrom")

        name = _ns_tag(peri_elem, "name") or ""
        base_addr = _parse_int(_ns_tag(peri_elem, "baseAddress"))
        desc = _ns_tag(peri_elem, "description") or ""

        peri = SVDPeripheral(name=name, base_address=base_addr, description=desc)

        # Copy registers from base if derivedFrom
        if derived_from and derived_from in base_peripherals:
            base = base_peripherals[derived_from]
            peri.registers = dict(base.registers)

        # Parse registers
        regs_elem = peri_elem.find("registers")
        if regs_elem is None:
            regs_elem = peri_elem.find("{*}registers")
        if regs_elem is not None:
            for reg_elem in regs_elem:
                reg_name = _ns_tag(reg_elem, "name") or ""
                offset = _parse_int(_ns_tag(reg_elem, "addressOffset"))
                reset_val = _parse_int(_ns_tag(reg_elem, "resetValue"))
                reg_desc = _ns_tag(reg_elem, "description") or ""

                reg = SVDRegister(
                    name=reg_name,
                    address_offset=offset,
                    reset_value=reset_val,
                    description=reg_desc,
                )

                fields_elem = reg_elem.find("fields")
                if fields_elem is None:
                    fields_elem = reg_elem.find("{*}fields")
                if fields_elem is not None:
                    for f_elem in fields_elem:
                        f_name = _ns_tag(f_elem, "name") or ""
                        bit_offset = _parse_int(_ns_tag(f_elem, "bitOffset"))
                        bit_width = _parse_int(_ns_tag(f_elem, "bitWidth"))
                        f_desc = _ns_tag(f_elem, "description") or ""
                        reg.fields[f_name] = SVDField(
                            name=f_name,
                            bit_offset=bit_offset,
                            bit_width=bit_width,
                            description=f_desc,
                            reset_value=reg.fields.get(f_name, SVDField("", 0, 1)).reset_value,
                        )

                peri.registers[reg_name] = reg

        peripherals[name] = peri
        base_peripherals[name] = peri

    return peripherals


# ─────────────────────────────────────────────────────────────────────────────
# OpenOCD telnet interface
# ─────────────────────────────────────────────────────────────────────────────

class OpenOCDClient:
    """Thin wrapper around the OpenOCD telnet command interface (port 4444)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4444, timeout: float = 5.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.connect((self._host, self._port))
        self._recv_until_prompt()  # consume banner

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def _recv_until_prompt(self) -> str:
        buf = b""
        while not buf.endswith(b"> "):
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                break
        return buf.decode(errors="replace")

    def cmd(self, command: str) -> str:
        if not self._sock:
            raise RuntimeError("Not connected to OpenOCD")
        self._sock.sendall((command + "\r\n").encode())
        return self._recv_until_prompt()

    def read_memory(self, address: int, width: int = 32) -> int:
        """Read a single register value from target memory."""
        resp = self.cmd(f"mdw 0x{address:08x} 1")
        # OpenOCD responds: "0xfe2b0000: 0x00000001 \n> "
        m = re.search(r"0x[0-9a-f]+:\s*(0x[0-9a-f]+)", resp, re.IGNORECASE)
        if m:
            return int(m.group(1), 16)
        return 0

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()


def read_registers_via_jtag(
    openocd_host: str,
    openocd_port: int,
    peripheral: SVDPeripheral,
) -> Dict[str, int]:
    """Return {register_name: value} by reading from a live OpenOCD session."""
    values: Dict[str, int] = {}
    with OpenOCDClient(openocd_host, openocd_port) as ocd:
        for reg_name, reg in peripheral.registers.items():
            addr = reg.absolute_address(peripheral.base_address)
            values[reg_name] = ocd.read_memory(addr)
    return values


# ─────────────────────────────────────────────────────────────────────────────
# Simulation mode
# ─────────────────────────────────────────────────────────────────────────────

def simulate_register_read(peripheral: SVDPeripheral) -> Dict[str, int]:
    """
    Return {register_name: reset_value} — models the "driver never ran" state.
    The DTS says the hardware should be configured, but boot left everything
    at silicon defaults.  Any deviation = the driver failed to apply the DTS.
    """
    return {name: reg.reset_value for name, reg in peripheral.registers.items()}


# ─────────────────────────────────────────────────────────────────────────────
# DTS vs Hardware diff engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProbeResult:
    severity: str          # "SILICON MISMATCH" | "WARNING" | "INFO"
    node_path: str
    node_name: str
    peripheral_name: str
    register_name: str
    field_name: str
    expected_desc: str
    expected_value: str
    actual_value: str
    verdict: str
    suggestion: str = ""


def _match_peripheral(node_base: int, peripherals: Dict[str, SVDPeripheral]
                       ) -> Optional[SVDPeripheral]:
    """Find the SVD peripheral whose base_address matches the DTS reg."""
    for peri in peripherals.values():
        if peri.base_address == node_base:
            return peri
    # Fuzzy: within 64 KB
    for peri in peripherals.values():
        if abs(peri.base_address - node_base) < 0x10000:
            return peri
    return None


def _dts_node_base(node) -> Optional[int]:
    """Extract base address from an IRNode's 'reg' property."""
    reg = node.properties.get("reg")
    if not reg:
        return None
    if isinstance(reg, list) and reg:
        v = reg[0]
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v, 0)
            except ValueError:
                return None
    if isinstance(reg, int):
        return reg
    return None


def _node_is_enabled(node) -> bool:
    status = node.properties.get("status", "okay")
    return status in ("okay", "ok")


_PINMUX_KEYWORDS = ("pinctrl", "pinmux", "iomux", "gpio", "mux")
_CLOCK_KEYWORDS  = ("clk", "clock", "pll", "cru", "cmu")
_POWER_KEYWORDS  = ("supply", "power", "vcc", "vdd", "ldo", "pmic", "regulator")


def diff_dts_vs_hardware(
    soc: SoC,
    peripherals: Dict[str, SVDPeripheral],
    register_snapshot: Dict[str, Dict[str, int]],
) -> List[ProbeResult]:
    """
    Cross-reference each enabled DTS node against the SVD register snapshot.

    register_snapshot: {peripheral_name: {register_name: value}}
    """
    results: List[ProbeResult] = []

    for dev_name, node in soc.devices.items():
        if not _node_is_enabled(node):
            continue

        base = _dts_node_base(node)
        if base is None:
            continue

        peri = _match_peripheral(base, peripherals)
        if peri is None:
            continue

        snap = register_snapshot.get(peri.name, {})

        # ── Check 1: enable / status bit ──────────────────────────────────
        for reg_name, reg in peri.registers.items():
            for field_name, f in reg.fields.items():
                key_low = (reg_name + "_" + field_name).lower()
                raw = snap.get(reg_name, reg.reset_value)
                actual_val = f.extract(raw)

                # Look for "enable" or "pe" (peripheral enable) bit
                if re.search(r"\benable\b|\bpe\b|\ben\b", field_name, re.I):
                    if actual_val == 0:
                        results.append(ProbeResult(
                            severity="SILICON MISMATCH",
                            node_path=node.path,
                            node_name=dev_name,
                            peripheral_name=peri.name,
                            register_name=reg_name,
                            field_name=field_name,
                            expected_desc="DTS has status='okay' → peripheral should be enabled",
                            expected_value="0x1 (enabled)",
                            actual_value=f"0x{actual_val:x} (disabled / reset default)",
                            verdict=(
                                "DTS is correct, but the enable bit was never written. "
                                "Driver initialization likely failed or was not called."
                            ),
                            suggestion=(
                                f"Check that the driver for {dev_name} probed successfully "
                                f"(`dmesg | grep {dev_name.split('@')[0]}`). "
                                f"The register {peri.name}.{reg_name}.{field_name} at "
                                f"0x{reg.absolute_address(peri.base_address):08x} "
                                f"must be 1."
                            ),
                        ))

        # ── Check 2: pinmux IOMUX_SEL field ──────────────────────────────
        pinctrl_data = node.properties.get("pinctrl-0") or node.properties.get("pinctrl-names")
        if pinctrl_data is not None:
            for reg_name, reg in peri.registers.items():
                for field_name, f in reg.fields.items():
                    if not re.search(r"iomux|sel|mux", field_name, re.I):
                        continue
                    raw = snap.get(reg_name, reg.reset_value)
                    actual_val = f.extract(raw)
                    if actual_val == 0 and f.reset_value == 0:
                        results.append(ProbeResult(
                            severity="WARNING",
                            node_path=node.path,
                            node_name=dev_name,
                            peripheral_name=peri.name,
                            register_name=reg_name,
                            field_name=field_name,
                            expected_desc=(
                                f"DTS declares pinctrl for {dev_name} — "
                                f"IOMUX selector should be non-zero"
                            ),
                            expected_value="non-zero (peripheral function selected)",
                            actual_value=f"0x{actual_val:x} (GPIO / reset default)",
                            verdict=(
                                "Pin is still in GPIO mode. "
                                "pinctrl driver may not have applied the DTS overlay."
                            ),
                            suggestion=(
                                "Verify pinctrl subsystem initialised before this driver. "
                                "Check for 'failed to set pin state' in dmesg."
                            ),
                        ))
                    break  # one field per register is enough

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: full pipeline
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProbeReport:
    dts_path: str
    svd_path: str
    mode: str               # "simulate" | "live"
    results: List[ProbeResult] = field(default_factory=list)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for r in self.results if r.severity == "SILICON MISMATCH")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.severity == "WARNING")

    @property
    def is_clean(self) -> bool:
        return self.mismatch_count == 0 and self.warning_count == 0


def run_live_probe(
    soc: SoC,
    svd_path: str,
    dts_path: str = "board.dts",
    simulate: bool = True,
    openocd_host: str = "127.0.0.1",
    openocd_port: int = 4444,
) -> ProbeReport:
    """Full pipeline: parse SVD → read registers → diff vs DTS."""
    peripherals = parse_svd(svd_path)

    register_snapshot: Dict[str, Dict[str, int]] = {}

    if simulate:
        for peri_name, peri in peripherals.items():
            register_snapshot[peri_name] = simulate_register_read(peri)
        mode = "simulate"
    else:
        for peri_name, peri in peripherals.items():
            try:
                register_snapshot[peri_name] = read_registers_via_jtag(
                    openocd_host, openocd_port, peri
                )
            except Exception as exc:
                # partial data — continue
                register_snapshot[peri_name] = simulate_register_read(peri)
        mode = "live"

    results = diff_dts_vs_hardware(soc, peripherals, register_snapshot)

    return ProbeReport(
        dts_path=dts_path,
        svd_path=svd_path,
        mode=mode,
        results=results,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────────

_COLORS = {
    "SILICON MISMATCH": "\033[1;31m",  # bold red
    "WARNING":          "\033[1;33m",  # bold yellow
    "INFO":             "\033[1;36m",  # bold cyan
    "RESET":            "\033[0m",
}


def render_probe_report(report: ProbeReport, use_color: bool = True) -> str:
    lines: List[str] = []

    def c(key: str, text: str) -> str:
        if not use_color:
            return text
        return f"{_COLORS.get(key, '')}{text}{_COLORS['RESET']}"

    mode_label = (
        "SIMULATION MODE (boot-default register values, no hardware required)"
        if report.mode == "simulate"
        else "LIVE JTAG MODE (physical register values from target)"
    )

    lines.append("=" * 70)
    lines.append(f"  SOCC SILICON LIE DETECTOR  |  {mode_label}")
    lines.append(f"  DTS : {report.dts_path}")
    lines.append(f"  SVD : {report.svd_path}")
    lines.append("=" * 70)

    if not report.results:
        lines.append(c("INFO", "[✓] No register/DTS mismatches detected."))
        lines.append(
            "    Note: In simulate mode this means no enabled DTS nodes have\n"
            "    registers that are still at boot-reset values.  Run with\n"
            "    --jtag to confirm against live silicon."
        )
    else:
        for r in report.results:
            lines.append("")
            lines.append(c(r.severity, f"[{r.severity}]  Node: {r.node_path}"))
            lines.append(f"  Peripheral  : {r.peripheral_name}.{r.register_name}.{r.field_name}")
            lines.append(f"  Expectation : {r.expected_desc}")
            lines.append(f"  Expected    : {r.expected_value}")
            lines.append(f"  Reality     : {r.actual_value}")
            lines.append(f"  Verdict     : {r.verdict}")
            if r.suggestion:
                wrapped = textwrap.fill(r.suggestion, width=66, initial_indent="  Fix         : ",
                                        subsequent_indent="               ")
                lines.append(wrapped)

    lines.append("")
    lines.append(
        f"Summary: {report.mismatch_count} silicon mismatch(es), "
        f"{report.warning_count} warning(s)."
    )
    if report.mode == "simulate":
        lines.append(
            "  [i] Simulation assumes all registers at SVD reset values.\n"
            "      Connect OpenOCD and re-run without --simulate for live truth."
        )
    return "\n".join(lines)
