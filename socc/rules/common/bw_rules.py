"""Interconnect / DDR bandwidth saturation rules (BW-1xx series).

Static analysis cannot measure runtime bandwidth, but a DTS reveals
*which* high-bandwidth peripherals are enabled.  These rules estimate
aggregate peak bandwidth from known per-peripheral budgets and warn when
the total approaches or exceeds the DDR controller limit.

BW-101  Estimated DDR peak bandwidth exceeds controller limit
BW-102  High-bandwidth peripheral enabled without QoS configuration

The bandwidth figures below are conservative worst-case estimates for
common IP blocks at typical clock rates.  They are intentionally broad —
the goal is to flag *obviously* over-provisioned designs, not to provide
cycle-accurate simulation.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Tuple

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Bandwidth database ─────────────────────────────────────────────────────────
# Maps compatible-string keyword → (label, peak_mb_s)
# Figures are peak sustained MB/s at common clock rates.


_BW_CONSUMERS: List[Tuple[str, str, int]] = [
    # (compat_keyword, label, peak_MB_s)
    # ── Video decode / encode ─────────────────────────────────────────────────
    ("rkvdec",   "Video Decoder (RK VDec)",         2500),
    ("rkvenc",   "Video Encoder (RK VEnc)",         2500),
    ("vepu",     "Video Encoder (VePU)",             2000),
    ("vdec",     "Video Decoder",                   2000),
    ("venc",     "Video Encoder",                   2000),
    ("hantro",   "Video Codec (Hantro)",            3000),
    # ── VOP / Display ─────────────────────────────────────────────────────────
    ("vop2",     "Display VOP2 (4K60)",             4200),
    ("vop",      "Display VOP",                     3000),
    # ── Image Signal Processor ────────────────────────────────────────────────
    ("rkisp1",   "ISP (RKISP1)",                   2100),
    ("rkisp",    "ISP (RKISP)",                    2100),
    ("isp",      "Image Signal Processor",          2000),
    # ── GPU ───────────────────────────────────────────────────────────────────
    ("mali-g610","Mali-G610 GPU (RK3588)",          8000),
    ("mali-g52", "Mali-G52 GPU",                   4000),
    ("mali",     "Mali GPU",                        4000),
    ("panfrost", "GPU (Panfrost driver)",           4000),
    ("lima",     "GPU (Lima driver)",               1500),
    # ── NPU ───────────────────────────────────────────────────────────────────
    ("rknn",     "NPU (RKNN)",                      5000),
    ("npu",      "Neural Processing Unit",          4000),
    # ── PCIe ──────────────────────────────────────────────────────────────────
    ("pcie3x4",  "PCIe 3.0 x4",                    3500),
    ("pcie3x2",  "PCIe 3.0 x2",                    1800),
    ("pcie2x1",  "PCIe 2.0 x1",                     450),
    ("pcie",     "PCIe Controller",                 1000),
    # ── USB ───────────────────────────────────────────────────────────────────
    ("xhci",     "USB3 XHCI",                        600),
    ("dwc3",     "USB3 DWC3",                        600),
    # ── Ethernet ──────────────────────────────────────────────────────────────
    ("gmac",     "GbE GMAC",                         125),
    ("emac",     "Fast Ethernet EMAC",                12),
    # ── EMMC / SD ─────────────────────────────────────────────────────────────
    ("sdhci",    "eMMC SDHCI (HS400)",               400),
    ("dwmmc",    "eMMC DW-MSHC",                     300),
    ("sdmmc",    "SD Card Controller",               100),
]


# DDR controller limits by known compatible strings (MB/s)
_DDR_LIMITS: Dict[str, int] = {
    "rockchip,rk3588":  51200,   # LPDDR5-6400 * 4 ch / 8 = ~51.2 GB/s
    "rockchip,rk3568":  17100,   # LPDDR4-4266 2ch ~17.1 GB/s
    "rockchip,rk3399":  17100,
    "allwinner,sun50i": 10000,
    "generic":          17000,   # conservative default
}
_DDR_DEFAULT_LIMIT = 17000   # MB/s (conservative LPDDR4 dual-channel)
_BW_WARN_RATIO     = 0.80    # warn at 80% of limit
_BW_ERROR_RATIO    = 0.95    # error at 95% of limit

# QoS capability keywords — nodes that can shape bandwidth
_QOS_KEYWORDS: FrozenSet[str] = frozenset({
    "qos", "noc", "dmc", "dfi", "devfreq",
})


def _get_compatible_str(props: dict) -> str:
    compat = props.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return " ".join(str(c) for c in compat).lower()
    return str(compat).lower()


def _is_enabled(props: dict) -> bool:
    status = props.get("status", "okay")
    return status in ("okay", "ok", "")


def _detect_ddr_limit(model: SoC) -> int:
    """Guess DDR controller limit from the SoC's device tree."""
    for _, node in model.devices.items():
        compat = _get_compatible_str(node.properties)
        for soc_key, limit in _DDR_LIMITS.items():
            if soc_key in compat:
                return limit
    return _DDR_DEFAULT_LIMIT


# ── BW-101 ────────────────────────────────────────────────────────────────────


class BW101DDRBandwidthSaturation(BaseRule):
    """BW-101: Aggregate estimated peak DDR bandwidth exceeds safe threshold.

    Sums the worst-case bandwidth requirements of all *enabled*
    high-bandwidth peripherals and compares the total against the DDR
    controller's theoretical peak.  Warns at ≥80%, errors at ≥95%.
    """

    code = "BW-101"
    name = "DDR Bandwidth Saturation Risk"
    description = (
        "The sum of enabled high-bandwidth peripheral peak bandwidth "
        "budgets meets or exceeds the DDR controller's theoretical limit.  "
        "Concurrent operation of these peripherals may cause frame drops, "
        "audio underruns, or DMA stalls."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        ddr_limit = _detect_ddr_limit(model)
        total_bw = 0
        active_consumers: List[Tuple[str, str, int]] = []

        for dev_name, dev_node in model.devices.items():
            if not _is_enabled(dev_node.properties):
                continue
            compat = _get_compatible_str(dev_node.properties)
            dev_lower = dev_name.lower()

            for kw, label, peak_mb in _BW_CONSUMERS:
                if kw in compat or kw in dev_lower:
                    total_bw += peak_mb
                    active_consumers.append((dev_name, label, peak_mb))
                    break  # only count once per device

        if not active_consumers:
            return []

        ratio = total_bw / ddr_limit
        if ratio < _BW_WARN_RATIO:
            return []

        sev = "error" if ratio >= _BW_ERROR_RATIO else "warning"
        pct = int(ratio * 100)

        consumer_lines = "\n".join(
            f"  {name}: {label} ({mb_s:,} MB/s)"
            for name, label, mb_s in sorted(active_consumers, key=lambda t: -t[2])
        )

        violations.append(
            self._create_violation(
                message=(
                    f"Estimated peak DDR bandwidth = {total_bw:,} MB/s "
                    f"({pct}% of {ddr_limit:,} MB/s theoretical limit)."
                ),
                impact=(
                    f"Concurrent operation of all {len(active_consumers)} enabled "
                    f"high-bandwidth peripherals may saturate the DDR bus, causing "
                    f"frame drops, audio glitches, DMA stalls, or CPU cache "
                    f"thrashing under real-world workloads.\n"
                    f"Top consumers:\n{consumer_lines}"
                ),
                suggestion=(
                    "Enable DDR frequency scaling (devfreq / DMC) so the controller "
                    "boosts frequency under load.  Configure QoS weights via the "
                    "NoC QoS nodes to prioritise latency-sensitive paths (display, "
                    "audio).  Consider whether all listed peripherals genuinely need "
                    "to run simultaneously at peak rate."
                ),
                location="/",
                affected_nodes=[name for name, _, _ in active_consumers],
                severity=sev,
            )
        )

        return violations


# ── BW-102 ────────────────────────────────────────────────────────────────────


class BW102HighBWWithoutQoS(BaseRule):
    """BW-102: High-bandwidth peripheral enabled without QoS / devfreq node.

    When a GPU, NPU, VPU, or ISP is present there should be a corresponding
    NoC QoS or devfreq node to shape bandwidth.  Without it, a workload on
    one peripheral can starve another.
    """

    code = "BW-102"
    name = "High-Bandwidth Peripheral Without QoS Configuration"
    description = (
        "A high-bandwidth peripheral (GPU, NPU, VPU, ISP, VOP) is enabled "
        "but no QoS / NoC bandwidth-shaping or devfreq (DRAM frequency "
        "governor) node is present in the DTS."
    )
    severity = "info"

    # Peripherals that warrant QoS concern
    _QOS_REQUIRED: FrozenSet[str] = frozenset({
        "gpu", "mali", "panfrost", "lima",
        "npu", "rknn",
        "vop", "vop2",
        "rkvdec", "rkvenc", "vepu", "vdec",
        "rkisp", "rkisp1", "isp",
    })

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        has_qos_node = any(
            _get_compatible_str(n.properties).__contains__(kw) or kw in name.lower()
            for name, n in model.devices.items()
            for kw in _QOS_KEYWORDS
        )

        if has_qos_node:
            return []   # QoS / devfreq is present — OK

        flagged: List[str] = []
        for dev_name, dev_node in model.devices.items():
            if not _is_enabled(dev_node.properties):
                continue
            compat = _get_compatible_str(dev_node.properties)
            dev_lower = dev_name.lower()
            if any(kw in compat or kw in dev_lower for kw in self._QOS_REQUIRED):
                flagged.append(dev_name)

        if not flagged:
            return []

        violations.append(
            self._create_violation(
                message=(
                    f"{len(flagged)} high-bandwidth peripheral(s) enabled without "
                    f"a QoS/devfreq node: {', '.join(flagged[:5])}"
                    + (" …" if len(flagged) > 5 else ".")
                ),
                impact=(
                    "Without bandwidth shaping, a GPU or NPU workload can starve "
                    "display and audio DMA paths, causing visual tearing, audio "
                    "dropouts, or system instability under concurrent workloads."
                ),
                suggestion=(
                    "Add a DMC (DRAM Frequency Controller) node and/or NoC QoS "
                    "nodes for the critical peripherals.  For Rockchip SoCs, "
                    "enable 'rockchip,rk3588-dmc' and set OPP tables. "
                    "Configure QoS weights in the system NoC driver."
                ),
                location="/",
                affected_nodes=flagged,
            )
        )

        return violations


# ── Registration ──────────────────────────────────────────────────────────────


def register_bw_rules(registry, soc_name: str = "common") -> None:
    """Register BW-1xx bandwidth rules."""
    registry.register(BW101DDRBandwidthSaturation(), soc_name)
    registry.register(BW102HighBWWithoutQoS(), soc_name)


__all__ = [
    "BW101DDRBandwidthSaturation",
    "BW102HighBWWithoutQoS",
    "register_bw_rules",
]
