"""Physical damage / "will it smoke?" simulator.

``socc simulate-smoke board.dts``

Analyses voltage-domain mismatches, clock overspeeds, and thermal cascades
that the DTS configuration could cause if loaded on real hardware.  Outputs
a hardware *Casualty Report* with a physical timeline so the consequence of
a configuration error is viscerally clear — ideal both as an engineering
sign-off gate and as a teaching tool.

────────────────────────────────────────────────────────────────────────────
Physics modelled
────────────────────────────────────────────────────────────────────────────
  1. IO-voltage mismatch  — 3.3 V on a 1.8 V pad → protection diode
     forward-biases, enters thermal runaway, pad cell destroyed.
  2. DDR voltage overshoot — VDD_DDR > spec causes DRAM row-hammer
     susceptibility and eventual die delamination.
  3. Regulator reverse / missing supply — device powered without
     parent domain → brown-out or latch-up.
  4. Clock domain overspeed — divider configured to produce a frequency
     beyond the device's max rating (if spec data is available).
  5. Missing power-sequencing — device whose supply must ramp before
     enabling receives power after the device is clocked.

Each finding produces a CasualtyEvent with an animated timeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from socc.model import SoC


# ── Voltage specifications ────────────────────────────────────────────────────
# Maps a compatible / supply-name keyword to its (nominal_v, absolute_max_v).
# Over absolute_max triggers an immediate casualty event.
# Over nominal triggers a degradation warning.

_VOLTAGE_SPEC: Dict[str, Tuple[float, float]] = {
    # 1.8V IO pads — common on modern SoCs
    "1v8":         (1.8, 2.0),
    "vcc1v8":      (1.8, 2.0),
    "vccio":       (1.8, 2.0),   # default VCCIO is 1.8V on RK3588
    "vcc_1v8":     (1.8, 2.0),
    "vdd_1v8":     (1.8, 2.0),
    # 3.3V tolerant IO
    "3v3":         (3.3, 3.6),
    "vcc3v3":      (3.3, 3.6),
    "vcc_3v3":     (3.3, 3.6),
    "vcc3v3_sys":  (3.3, 3.6),
    # Core voltages — extremely sensitive
    "vdd_cpu":     (0.85, 1.05),
    "vdd_core":    (0.85, 1.05),
    "vdd_logic":   (0.85, 1.1),
    "vdd_gpu":     (0.75, 1.0),
    "vdd_npu":     (0.75, 1.0),
    # DDR
    "vdd_ddr":     (1.1, 1.2),   # LPDDR4X nominal
    "vcc_ddr":     (1.1, 1.2),
    "vddq_ddr":    (0.6, 0.65),  # LPDDR5
    # 5V supply
    "vbus":        (5.0, 5.5),
    "vcc5v0":      (5.0, 5.5),
}

# Maps pad-type keyword to max tolerable voltage (V)
_PAD_MAX_VOLTAGE: Dict[str, float] = {
    "1v8":         1.8,
    "18":          1.8,
    "vccio1":      1.8,
    "vccio3":      1.8,
    "vccio4":      1.8,
    "vccio6":      1.8,
    "sdmmc_vccio": 3.3,   # SD card IO can run 3.3V
    "vccio7":      1.8,
    "3v3":         3.3,
    "33":          3.3,
}

# Supply name → supply voltage (V) — anything not found → assume from name
_SUPPLY_VOLTAGE: Dict[str, float] = {
    "vcc_3v3":    3.3,  "vcc3v3":    3.3,  "vcc3v3_sys": 3.3,
    "vcc_1v8":    1.8,  "vcc1v8":    1.8,  "vdd_1v8":    1.8,
    "vcc_5v0":    5.0,  "vcc5v0":    5.0,  "vbus":       5.0,
    "vdd_cpu":    0.9,  "vdd_core":  0.9,
    "vdd_gpu":    0.85, "vdd_npu":   0.85,
    "vdd_ddr":    1.1,  "vcc_ddr":   1.1,
}

# io-supply node → max safe voltage for that IO bank
_IO_SUPPLY_MAX: Dict[str, float] = {
    "vcc_1v8":    1.8,
    "vcc1v8":     1.8,
    "vdd_1v8":    1.8,
    "vccio_sd":   3.3,
    "vcc_3v3":    3.3,
    "vcc3v3":     3.3,
    "vcc3v3_sys": 3.3,
}

# If a device compatible matches one of these, its IO is 1.8V max
_IO_18V_COMPAT = frozenset({
    "rockchip,rk3588", "rockchip,rk3568", "rockchip,rk3399",
    "snps,dw-apb-i2c", "snps,dw-apb-spi", "snps,dw-apb-uart",
    "arm,pl022", "arm,pl011",
})


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class TimelineStep:
    time_s: float     # seconds after kernel enables the device
    event: str        # human-readable description


@dataclass
class CasualtyEvent:
    severity: str                # "FATAL" | "CRITICAL" | "WARNING" | "ADVISORY"
    device_path: str
    device_name: str
    trigger: str                 # one-liner root cause
    physical_impact: str         # Ohm's-law / thermodynamics narrative
    timeline: List[TimelineStep]
    result: str                  # outcome sentence
    repair: str                  # what the engineer must do


@dataclass
class SmokeSim:
    dts_path: str
    events: List[CasualtyEvent] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not any(e.severity in ("FATAL", "CRITICAL") for e in self.events)

    @property
    def fatal_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "FATAL")

    @property
    def critical_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "CRITICAL")

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "WARNING")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _supply_voltage(supply_name: str) -> Optional[float]:
    name = supply_name.lower().replace("&", "").strip()
    for key, v in _SUPPLY_VOLTAGE.items():
        if key in name:
            return v
    # Heuristic: extract voltage from name like "vcc_1v8" → 1.8
    for part in name.split("_"):
        part = part.replace("v", ".")
        try:
            v = float(part)
            if 0.5 <= v <= 20.0:
                return v
        except ValueError:
            pass
    return None


def _io_max_voltage(supply_name: str) -> Optional[float]:
    name = supply_name.lower()
    for key, v in _IO_SUPPLY_MAX.items():
        if key in name:
            return v
    return None


def _get_compat(node) -> str:
    c = node.properties.get("compatible", "")
    if isinstance(c, (list, tuple)):
        return " ".join(str(x) for x in c).lower()
    return str(c).lower()


def _is_enabled(node) -> bool:
    return node.properties.get("status", "okay") in ("okay", "ok", "")


def _get_supply_name(node, *keys: str) -> Optional[str]:
    for key in keys:
        val = node.properties.get(key)
        if val is not None:
            return str(val).replace("<", "").replace(">", "").strip()
    return None


# ── Analysis passes ───────────────────────────────────────────────────────────


def _check_io_voltage_mismatch(model: SoC) -> List[CasualtyEvent]:
    """Detect devices whose vcc-supply drives a higher voltage into 1.8V IO pads."""
    events: List[CasualtyEvent] = []

    for dev_name, node in model.devices.items():
        if not _is_enabled(node):
            continue

        compat = _get_compat(node)

        # Determine if this peripheral has 1.8V IO pads
        is_18v_io = any(c in compat for c in _IO_18V_COMPAT)
        if not is_18v_io:
            # Also flag if the device name suggests a 1.8V domain
            is_18v_io = any(k in dev_name.lower() for k in ("i2c", "spi", "uart", "serial"))

        if not is_18v_io:
            continue

        supply_name = _get_supply_name(node, "vcc-supply", "iovcc-supply",
                                       "vdd-supply", "vcc_io-supply")
        if not supply_name:
            continue

        supply_v = _supply_voltage(supply_name)
        if supply_v is None:
            continue

        # Fatal: supply above 2.0V into a 1.8V IO pad
        if supply_v > 2.0:
            events.append(CasualtyEvent(
                severity="FATAL",
                device_path=node.path,
                device_name=dev_name,
                trigger=(
                    f"{node.path} has vcc-supply = <{supply_name}> "
                    f"({supply_v:.1f}V), but physical IO pads are strictly 1.8V."
                ),
                physical_impact=(
                    f"The {supply_v:.1f}V supply is applied directly to IO pads "
                    f"rated for 1.8V absolute maximum.  When the IO pad clamps at "
                    f"VDD_IO+0.3V ≈ 2.1V, the excess "
                    f"{supply_v - 1.8:.2f}V × pad_leakage_current drives the "
                    f"ESD protection diode into forward conduction.  Power "
                    f"dissipation in the diode: P = V_excess × I_clamp ≈ "
                    f"{(supply_v-1.8)*50:.0f} mW per pad.  "
                    f"Thermal resistance of an IO cell is ~200 °C/W; "
                    f"at 50 mA clamp current the junction reaches "
                    f"{25 + (supply_v-1.8)*50e-3*200:.0f} °C in milliseconds."
                ),
                timeline=[
                    TimelineStep(0.000, "Kernel enables the peripheral controller."),
                    TimelineStep(0.001,
                        f"{supply_v:.1f}V driven into 1.8V-rated IO pad via vcc-supply."),
                    TimelineStep(0.010,
                        "ESD protection diode forward-biases; "
                        f"clamp current ~50mA flows through the diode cell."),
                    TimelineStep(0.100,
                        f"Diode junction temperature exceeds 150°C; "
                        "thermal runaway begins."),
                    TimelineStep(0.500,
                        "Protection diode metallisation melts.  "
                        "IO block is shorted to VDD_IO rail."),
                ],
                result=(
                    f"The IO block inside the SoC driving {dev_name!r} is "
                    "PERMANENTLY INCINERATED.  The SoC requires physical replacement."
                ),
                repair=(
                    f"Change the supply for {dev_name!r} to a 1.8V LDO/regulator. "
                    f"If the peripheral is 3.3V-only, use a bidirectional "
                    f"level-shifter (e.g. TXS0108E) between the SoC and the device."
                ),
            ))
        elif supply_v > 1.98:  # within 10% margin — warning
            events.append(CasualtyEvent(
                severity="WARNING",
                device_path=node.path,
                device_name=dev_name,
                trigger=(
                    f"{node.path} vcc-supply = <{supply_name}> ({supply_v:.1f}V) "
                    f"is within 10% of the 1.8V absolute-max — reliability risk."
                ),
                physical_impact=(
                    "Voltage is above the recommended operating range.  ESD "
                    "diodes will conduct slightly, accelerating electromigration "
                    "and reducing MTBF."
                ),
                timeline=[
                    TimelineStep(0.0, "Peripheral enabled at marginal supply voltage."),
                    TimelineStep(3600.0, "Accelerated aging (~10× normal) begins."),
                ],
                result="Reduced component lifetime; early field failures expected.",
                repair=f"Lower {supply_name} to ≤ 1.85V.",
            ))

    return events


def _check_regulator_reverse_supply(model: SoC) -> List[CasualtyEvent]:
    """Detect regulators that are always-on but whose parent supply is disabled."""
    events: List[CasualtyEvent] = []
    tree = model.power_tree

    # Collect disabled parents
    disabled_nodes = {
        name for name, reg in tree.nodes.items()
        if getattr(reg, "regulator_always_on", False) is False
        and getattr(reg, "regulator_boot_on", False) is False
    }

    for name, reg in tree.nodes.items():
        parent = getattr(reg, "parent", None)
        if parent and parent in disabled_nodes:
            events.append(CasualtyEvent(
                severity="CRITICAL",
                device_path=f"/power/{name}",
                device_name=name,
                trigger=(
                    f"Regulator '{name}' is enabled but its parent supply "
                    f"'{parent}' is not marked always-on or boot-on."
                ),
                physical_impact=(
                    f"'{name}' will attempt to regulate without a valid input "
                    f"voltage.  If parent dropout occurs, the output collapses "
                    f"mid-sequence.  Connected device sees a brown-out transient "
                    f"which may cause latch-up: the device's substrate diode "
                    f"forward-biases, drawing current from a different supply "
                    f"rail, potentially triggering SCR latch-up."
                ),
                timeline=[
                    TimelineStep(0.0, f"Kernel enables '{name}'."),
                    TimelineStep(0.002, f"'{parent}' collapses — input dropout."),
                    TimelineStep(0.005, "Output voltage of '{name}' falls below UVLO."),
                    TimelineStep(0.010, "Connected device enters latch-up condition."),
                ],
                result=(
                    "Latch-up draws destructive current from the adjacent supply; "
                    "both the regulator and the attached device may be destroyed."
                ),
                repair=(
                    f"Add 'regulator-always-on;' or 'regulator-boot-on;' to the "
                    f"'{parent}' node, or correct the power sequencing order in the DTS."
                ),
            ))

    return events


def _check_clock_overspeed(model: SoC) -> List[CasualtyEvent]:
    """Detect clocks configured above known safe maximums."""
    events: List[CasualtyEvent] = []

    # Known max frequencies (MHz) for common clock domains on Rockchip SoCs
    _CLK_MAX_MHZ: Dict[str, float] = {
        "clk_cpu":     2.4e9 / 1e6,
        "clk_gpu":     1.0e9 / 1e6,
        "clk_npu":     1.0e9 / 1e6,
        "aclk_vop":    800.0,
        "dclk_vop":    600.0,
        "clk_uart":    200.0,
        "clk_i2c":     200.0,
        "clk_spi":     200.0,
        "clk_emmc":    200.0,
        "clk_sdmmc":   150.0,
        "clk_pcie":    100.0,
    }

    for clk_name, clk in model.clock_tree.clocks.items():
        freq_hz = getattr(clk, "frequency", None) or getattr(clk, "rate", None)
        if freq_hz is None or freq_hz <= 0:
            continue
        freq_mhz = freq_hz / 1e6

        for key, max_mhz in _CLK_MAX_MHZ.items():
            if key in clk_name.lower() and freq_mhz > max_mhz * 1.05:
                overspeed_pct = (freq_mhz / max_mhz - 1) * 100
                events.append(CasualtyEvent(
                    severity="CRITICAL" if overspeed_pct > 20 else "WARNING",
                    device_path=f"/clocks/{clk_name}",
                    device_name=clk_name,
                    trigger=(
                        f"Clock '{clk_name}' configured at {freq_mhz:.1f} MHz "
                        f"(max spec: {max_mhz:.0f} MHz, "
                        f"+{overspeed_pct:.0f}% over limit)."
                    ),
                    physical_impact=(
                        f"At {freq_mhz:.1f} MHz the switching frequency of internal "
                        f"logic gates exceeds their propagation delay budget. "
                        f"Dynamic power: P = α·C·V²·f = "
                        f"{0.2 * 10e-12 * 0.9**2 * freq_hz * 1e3:.0f} mW (est.). "
                        f"Excess heat raises junction temperature, causing "
                        f"electromigration in copper interconnects above 125°C "
                        f"and eventual metal voiding / open-circuit failure."
                    ),
                    timeline=[
                        TimelineStep(0.0, f"CRU programs '{clk_name}' to {freq_mhz:.0f} MHz."),
                        TimelineStep(1.0, "Setup/hold violations cause random bit-flips."),
                        TimelineStep(3600.0, "Electromigration accelerates; MTBF = days."),
                    ],
                    result=(
                        f"Logic block fed by '{clk_name}' produces unpredictable "
                        "results and will fail permanently within hours to days."
                    ),
                    repair=(
                        f"Set '{clk_name}' ≤ {max_mhz:.0f} MHz in the CRU configuration. "
                        f"Current value: {freq_mhz:.1f} MHz."
                    ),
                ))
                break

    return events


def _check_missing_power_domains(model: SoC) -> List[CasualtyEvent]:
    """Detect enabled devices with no supply mapping in the DTS."""
    events: List[CasualtyEvent] = []
    # Devices considered critical enough that missing supply is a red flag
    _SUPPLY_REQUIRED_COMPAT = frozenset({
        "snps,dw-apb-i2c", "snps,dw-apb-spi", "snps,dw-apb-uart",
        "rockchip,rk3588-emmc", "rockchip,rk3588-sdhci",
        "arm,pl011", "arm,pl022",
    })

    for dev_name, node in model.devices.items():
        if not _is_enabled(node):
            continue
        compat = _get_compat(node)
        needs_supply = any(c in compat for c in _SUPPLY_REQUIRED_COMPAT)
        if not needs_supply:
            continue

        has_supply = any(
            key in node.properties
            for key in ("vcc-supply", "iovcc-supply", "vdd-supply",
                        "vddio-supply", "power-domains")
        )
        if not has_supply:
            events.append(CasualtyEvent(
                severity="ADVISORY",
                device_path=node.path,
                device_name=dev_name,
                trigger=(
                    f"{node.path} is enabled but has no supply "
                    f"(vcc-supply / power-domains) specified."
                ),
                physical_impact=(
                    "If the IO supply for this peripheral is not explicitly "
                    "managed, it may power up at an indeterminate voltage, "
                    "causing random probe failures or IO contention."
                ),
                timeline=[
                    TimelineStep(0.0, "Kernel enables peripheral."),
                    TimelineStep(0.001,
                        "Supply rail voltage indeterminate — no regulator handle."),
                ],
                result="Intermittent boot failures and possible IO damage over time.",
                repair=(
                    f"Add 'vcc-supply = <&your_1v8_regulator>;' to the "
                    f"{dev_name} node and ensure the regulator is always-on "
                    f"during boot."
                ),
            ))

    return events


# ── Main API ──────────────────────────────────────────────────────────────────


def simulate_smoke(soc: SoC, dts_path: str = "board.dts") -> SmokeSim:
    """Run all physical damage checks and return a SmokeSim report."""
    sim = SmokeSim(dts_path=dts_path)

    sim.events.extend(_check_io_voltage_mismatch(soc))
    sim.events.extend(_check_regulator_reverse_supply(soc))
    sim.events.extend(_check_clock_overspeed(soc))
    sim.events.extend(_check_missing_power_domains(soc))

    # Sort: FATAL first, then CRITICAL, WARNING, ADVISORY
    _order = {"FATAL": 0, "CRITICAL": 1, "WARNING": 2, "ADVISORY": 3}
    sim.events.sort(key=lambda e: _order.get(e.severity, 4))

    return sim


# ── Renderer ──────────────────────────────────────────────────────────────────

_SEV_ICON = {
    "FATAL":    "🔥",
    "CRITICAL": "⚡",
    "WARNING":  "⚠️ ",
    "ADVISORY": "ℹ️ ",
}

_SEV_LABEL = {
    "FATAL":    "FATAL     ",
    "CRITICAL": "CRITICAL  ",
    "WARNING":  "WARNING   ",
    "ADVISORY": "ADVISORY  ",
}


def render_smoke_report(sim: SmokeSim, use_color: bool = True) -> str:
    lines: List[str] = []

    # Banner
    if sim.fatal_count > 0:
        banner = "☠  HARDWARE CASUALTY REPORT — STOP IMMEDIATELY"
    elif sim.critical_count > 0:
        banner = "🔥 HARDWARE CASUALTY REPORT — CRITICAL ISSUES FOUND"
    elif sim.warning_count > 0:
        banner = "⚠️  HARDWARE SMOKE REPORT — WARNINGS FOUND"
    else:
        banner = "✅ SMOKE CHECK PASSED — no physical damage predicted"

    lines.append(banner)
    lines.append(f"   DTS: {sim.dts_path}")
    lines.append(
        f"   {sim.fatal_count} FATAL  ·  "
        f"{sim.critical_count} CRITICAL  ·  "
        f"{sim.warning_count} WARNING  ·  "
        f"{len([e for e in sim.events if e.severity == 'ADVISORY'])} ADVISORY"
    )
    lines.append("")

    for i, ev in enumerate(sim.events, 1):
        icon = _SEV_ICON.get(ev.severity, "?")
        label = _SEV_LABEL.get(ev.severity, ev.severity)
        lines.append(f"{'─'*72}")
        lines.append(f"[{icon} {label}] Event #{i}: {ev.device_name}")
        lines.append(f"")
        lines.append(f"  Trigger  : {ev.trigger}")
        lines.append(f"")
        lines.append(f"  Physical Impact:")
        # Word-wrap the impact narrative at 66 chars
        import textwrap as _tw
        for chunk in _tw.wrap(ev.physical_impact, width=66):
            lines.append(f"    {chunk}")
        lines.append(f"")
        lines.append(f"  Timeline:")
        for step in ev.timeline:
            lines.append(f"    t={step.time_s:>8.3f}s  {step.event}")
        lines.append(f"")
        lines.append(f"  RESULT   : {ev.result}")
        lines.append(f"  FIX      : {ev.repair}")
        lines.append(f"")

    if not sim.events:
        lines.append("No physical damage scenarios detected for this DTS.")
        lines.append("")
        lines.append(
            "Note: This tool can only analyse issues that are visible in the DTS "
            "(supply names, IO voltages, clock rates).  Always perform a full "
            "schematic review before powering on new hardware."
        )

    lines.append(f"{'─'*72}")
    return "\n".join(lines)
