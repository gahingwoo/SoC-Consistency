"""Saleae Logic / Logic 2 workspace configuration generator.

Usage:
    socc generate-saleae board.dts  [--output board_saleae.json]

Generates a Saleae Logic 2 workspace JSON file that pre-configures:

  • Digital channels labelled after the actual DTS signal names
  • Protocol analyzers for detected I2C / SPI / UART / CAN buses
  • Sample rate recommendations based on bus clock rates

The output JSON can be imported directly via:
    Logic 2  →  File  →  Load Existing Capture / Setup

Note: Saleae Logic 2 workspace JSON schema is documented at
https://support.saleae.com/faq/technical-faq/export-format
This generator targets the "preset" (saved setup) format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from socc.model import SoC


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class SaleaeChannel:
    channel_index: int
    label: str
    enabled: bool = True
    digital: bool = True    # True = digital, False = analog


@dataclass
class SaleaeAnalyzer:
    type: str               # "I2C", "SPI", "Async Serial", "CAN"
    label: str
    settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SaleaeWorkspace:
    version: str = "2.0"
    sample_rate_hz: int = 8_000_000     # 8 MS/s default
    channels: List[SaleaeChannel] = field(default_factory=list)
    analyzers: List[SaleaeAnalyzer] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_compat(node) -> str:
    compat = node.properties.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return " ".join(str(c) for c in compat).lower()
    return str(compat).lower()


def _is_enabled(node) -> bool:
    return node.properties.get("status", "okay") in ("okay", "ok", "")


def _get_clock_hz(node) -> Optional[int]:
    """Try to extract a bus clock rate from DTS properties."""
    for key in ("clock-frequency", "max-frequency", "spi-max-frequency"):
        val = node.properties.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return None


def _recommended_sample_rate(bus_hz: Optional[int], analyzer_type: str) -> int:
    """Return a recommended Saleae sample rate (>= 4× oversampling)."""
    if bus_hz is None:
        # Defaults by protocol
        defaults = {
            "I2C": 8_000_000,
            "SPI": 16_000_000,
            "Async Serial": 4_000_000,
            "CAN": 4_000_000,
        }
        return defaults.get(analyzer_type, 8_000_000)
    oversample = 4 if analyzer_type in ("I2C", "Async Serial") else 8
    # Round up to nearest power-of-2 MHz
    target = bus_hz * oversample
    rate = 1_000_000
    while rate < target:
        rate *= 2
    return min(rate, 500_000_000)   # cap at 500 MS/s


# ── Bus classification ────────────────────────────────────────────────────────


_I2C_COMPAT = frozenset({"i2c", "twi"})
_SPI_COMPAT = frozenset({"spi", "qspi"})
_UART_COMPAT = frozenset({"uart", "serial", "16550", "pl011", "dw-apb-uart"})
_CAN_COMPAT  = frozenset({"can", "mcan", "flexcan"})


def _kw(text: str, kws) -> bool:
    return any(k in text for k in kws)


# ── Generator ─────────────────────────────────────────────────────────────────


def generate_saleae_workspace(soc: SoC, dts_name: str = "board.dts") -> Dict[str, Any]:
    """Return a Saleae Logic 2 workspace as a JSON-serialisable dict."""
    ws = SaleaeWorkspace()
    ch_index = 0
    max_sample_rate = ws.sample_rate_hz

    for dev_name, node in soc.devices.items():
        if not _is_enabled(node):
            continue
        compat = _get_compat(node)
        dev_lower = dev_name.lower()
        clock_hz = _get_clock_hz(node)

        # ── I2C bus ───────────────────────────────────────────────────────────
        if _kw(compat, _I2C_COMPAT) or _kw(dev_lower, _I2C_COMPAT):
            sda_idx = ch_index
            scl_idx = ch_index + 1
            label_base = dev_name.upper().replace("@", "_").replace("/", "_")

            ws.channels.append(SaleaeChannel(sda_idx, f"{label_base}_SDA"))
            ws.channels.append(SaleaeChannel(scl_idx, f"{label_base}_SCL"))

            rate = _recommended_sample_rate(clock_hz, "I2C")
            max_sample_rate = max(max_sample_rate, rate)

            ws.analyzers.append(SaleaeAnalyzer(
                type="I2C",
                label=f"{label_base} I2C",
                settings={
                    "SDA": sda_idx,
                    "SCL": scl_idx,
                },
            ))
            ch_index += 2

        # ── SPI bus ───────────────────────────────────────────────────────────
        elif _kw(compat, _SPI_COMPAT) or _kw(dev_lower, _SPI_COMPAT):
            mosi_idx = ch_index
            miso_idx = ch_index + 1
            clk_idx  = ch_index + 2
            cs_idx   = ch_index + 3
            label_base = dev_name.upper().replace("@", "_").replace("/", "_")

            ws.channels.extend([
                SaleaeChannel(mosi_idx, f"{label_base}_MOSI"),
                SaleaeChannel(miso_idx, f"{label_base}_MISO"),
                SaleaeChannel(clk_idx,  f"{label_base}_CLK"),
                SaleaeChannel(cs_idx,   f"{label_base}_CS"),
            ])

            rate = _recommended_sample_rate(clock_hz, "SPI")
            max_sample_rate = max(max_sample_rate, rate)

            ws.analyzers.append(SaleaeAnalyzer(
                type="SPI",
                label=f"{label_base} SPI",
                settings={
                    "MOSI": mosi_idx,
                    "MISO": miso_idx,
                    "Clock": clk_idx,
                    "Enable": cs_idx,
                    "Clock Phase": 0,
                    "Clock Polarity": 0,
                    "Significant Bit": "MSB First",
                    "Bits per Transfer": 8,
                },
            ))
            ch_index += 4

        # ── UART ──────────────────────────────────────────────────────────────
        elif _kw(compat, _UART_COMPAT) or _kw(dev_lower, _UART_COMPAT):
            tx_idx = ch_index
            rx_idx = ch_index + 1
            baud = clock_hz or 115200
            label_base = dev_name.upper().replace("@", "_").replace("/", "_")

            ws.channels.extend([
                SaleaeChannel(tx_idx, f"{label_base}_TX"),
                SaleaeChannel(rx_idx, f"{label_base}_RX"),
            ])

            rate = _recommended_sample_rate(baud, "Async Serial")
            max_sample_rate = max(max_sample_rate, rate)

            ws.analyzers.append(SaleaeAnalyzer(
                type="Async Serial",
                label=f"{label_base} UART",
                settings={
                    "Input Channel": tx_idx,
                    "Bit Rate (Bits/s)": baud,
                    "Bits per Frame": 8,
                    "Stop Bits": 1,
                    "Parity Bit": "No Parity Bit",
                    "Significant Bit": "LSB First",
                    "Signal inversion": "Non Inverted",
                },
            ))
            ch_index += 2

        # ── CAN ───────────────────────────────────────────────────────────────
        elif _kw(compat, _CAN_COMPAT) or _kw(dev_lower, _CAN_COMPAT):
            canh_idx = ch_index
            canl_idx = ch_index + 1
            bitrate = clock_hz or 500_000
            label_base = dev_name.upper().replace("@", "_").replace("/", "_")

            ws.channels.extend([
                SaleaeChannel(canh_idx, f"{label_base}_CANH"),
                SaleaeChannel(canl_idx, f"{label_base}_CANL"),
            ])

            rate = _recommended_sample_rate(bitrate, "CAN")
            max_sample_rate = max(max_sample_rate, rate)

            ws.analyzers.append(SaleaeAnalyzer(
                type="CAN",
                label=f"{label_base} CAN",
                settings={
                    "Input Channel": canh_idx,
                    "Bit Rate (Bits/s)": bitrate,
                },
            ))
            ch_index += 2

        if ch_index >= 64:   # Logic 2 Pro max channels
            break

    ws.sample_rate_hz = max_sample_rate

    # Serialise to plain dict (Saleae JSON schema)
    return {
        "version": ws.version,
        "sample_rate_hz": ws.sample_rate_hz,
        "source_dts": dts_name,
        "digital_channels": [
            {
                "channel_index": c.channel_index,
                "label": c.label,
                "enabled": c.enabled,
            }
            for c in ws.channels
        ],
        "analyzers": [
            {
                "type": a.type,
                "label": a.label,
                "settings": a.settings,
            }
            for a in ws.analyzers
        ],
    }


def render_saleae_workspace(soc: SoC, dts_name: str = "board.dts") -> str:
    """Return the workspace JSON as a formatted string."""
    data = generate_saleae_workspace(soc, dts_name)
    return json.dumps(data, indent=2)
