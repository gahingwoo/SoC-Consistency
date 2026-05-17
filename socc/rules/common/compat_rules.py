"""Vendor-kernel-to-mainline compatibility audit rules (COMP-1xx series).

COMP-101  Deprecated Vendor BSP Binding Detected

Vendor kernel trees (Rockchip, Allwinner, Amlogic, NXP, Qualcomm BSP) often
carry compatible strings, DT properties, or node names that were renamed,
split, or removed before or during mainline upstreaming.  A DTS written
against a vendor 5.10/6.1 BSP kernel may fail to compile, trigger binding
warnings, or silently misconfigure hardware under mainline 6.6+.

This module contains a curated database of known vendor-to-mainline binding
renames, maintained manually from upstream Linux DT binding changelogs and
commit history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Deprecated binding database ───────────────────────────────────────────────
#
# Format:
#   vendor_compatible_string: (mainline_replacement, since_kernel, description)
#
# "since_kernel" is the first mainline version where the replacement is
# required; the deprecated name may still compile on older kernels.

@dataclass
class _BindingEntry:
    replacement: str        # mainline compatible string
    since_kernel: str       # e.g. "6.8"
    description: str        # one-line explanation of what changed


_DEPRECATED_BINDINGS: Dict[str, _BindingEntry] = {

    # ── Rockchip ──────────────────────────────────────────────────────────────

    # VOP (display pipeline) — rk3588
    "rockchip,rk3588-vop-core": _BindingEntry(
        replacement="rockchip,rk3588-vop",
        since_kernel="6.8",
        description="VOP compatible split into -vop/-vop2; vendor BSP used -vop-core",
    ),
    "rockchip,rk3399-vop-big": _BindingEntry(
        replacement="rockchip,rk3288-vop",
        since_kernel="5.10",
        description="rk3399 VOP reuses rk3288 binding in mainline DRM",
    ),
    "rockchip,rk3399-vop-lit": _BindingEntry(
        replacement="rockchip,rk3288-vop",
        since_kernel="5.10",
        description="rk3399 VOP lit reuses rk3288 binding in mainline DRM",
    ),
    # Crypto
    "rockchip,rk3588s-crypto": _BindingEntry(
        replacement="rockchip,rk3588-crypto",
        since_kernel="6.7",
        description="rk3588s variant uses same binding as rk3588",
    ),
    # CRU / clock
    "rockchip,rk3568-pmu-cru": _BindingEntry(
        replacement="rockchip,rk3568-pmucru",
        since_kernel="5.15",
        description="Hyphen placement changed during upstream review",
    ),
    # RGA (2D accelerator)
    "rockchip,rga2": _BindingEntry(
        replacement="rockchip,rk3288-rga",
        since_kernel="6.1",
        description="RGA2 renamed to match the hardware generation",
    ),
    # ISP
    "rockchip,rkisp1": _BindingEntry(
        replacement="rockchip,rk3399-rkisp1",
        since_kernel="5.11",
        description="ISP requires SoC-specific suffix in mainline media tree",
    ),
    "rockchip,rk3568-hdmi": _BindingEntry(
        replacement="rockchip,rk3399-hdmi",
        since_kernel="6.0",
        description="rk3568 HDMI reuses rk3399 binding (same IP)",
    ),

    # ── Allwinner ─────────────────────────────────────────────────────────────

    "allwinner,sun50i-h616-ledc": _BindingEntry(
        replacement="allwinner,sun50i-h618-ledc",
        since_kernel="6.6",
        description="LED controller renamed when H618 diverged from H616",
    ),
    "allwinner,sun8i-h3-emac": _BindingEntry(
        replacement="allwinner,sun8i-h3-emac",
        since_kernel="4.13",
        description="Still correct for H3 but not A64; use sun50i-a64-emac for A64",
    ),
    "allwinner,sun6i-a31-dma": _BindingEntry(
        replacement="allwinner,sun50i-h5-dma",
        since_kernel="5.4",
        description="H5 DMA is a separate IP; vendor BSP incorrectly reused A31",
    ),
    "allwinner,sun50i-h616-ccu": _BindingEntry(
        replacement="allwinner,sun50i-h616-ccu",
        since_kernel="5.13",
        description="Correct binding — no change needed for mainline 5.13+",
    ),
    "allwinner,sun55i-a523-ppu": _BindingEntry(
        replacement="allwinner,sun55i-a523-ppu",
        since_kernel="6.9",
        description="PPU binding landed in 6.9; use exact string (no vendor alias)",
    ),
    # Display
    "allwinner,sun4i-a10-tcon": _BindingEntry(
        replacement="allwinner,sun8i-h3-tcon-tv",
        since_kernel="5.0",
        description="TCON-TV path on H3/H5 has its own binding",
    ),
    "allwinner,sun8i-a83t-de2-mixer": _BindingEntry(
        replacement="allwinner,sun50i-h5-de2-mixer-0",
        since_kernel="4.20",
        description="DE2 mixer binding is per-SoC in mainline",
    ),

    # ── Amlogic ───────────────────────────────────────────────────────────────

    "amlogic,meson-gxl-vdec": _BindingEntry(
        replacement="amlogic,gxl-vdec",
        since_kernel="5.15",
        description="Vendor used meson- prefix; mainline dropped it for vdec",
    ),
    "amlogic,meson-axg-sound-card": _BindingEntry(
        replacement="amlogic,axg-sound-card",
        since_kernel="5.5",
        description="ASoC sound card binding dropped meson- prefix on upstreaming",
    ),
    "amlogic,meson-g12b-ddr-pll": _BindingEntry(
        replacement="amlogic,g12a-ddr-pll",
        since_kernel="5.10",
        description="G12B DDR PLL reuses G12A binding in mainline clock driver",
    ),
    "amlogic,meson-sm1-ao-clkc": _BindingEntry(
        replacement="amlogic,meson-g12a-aoclkc",
        since_kernel="5.6",
        description="SM1 AO clock controller reuses G12A binding",
    ),

    # ── Qualcomm ──────────────────────────────────────────────────────────────

    "qcom,sdm845-camcc": _BindingEntry(
        replacement="qcom,sdm845-camcc",
        since_kernel="5.10",
        description="Correct mainline binding — no change needed",
    ),
    "qcom,msm-vidc": _BindingEntry(
        replacement="qcom,sm8250-venus",
        since_kernel="5.13",
        description="Venus video codec renamed per-SoC during upstreaming",
    ),
    "qcom,msm8998-mmcc": _BindingEntry(
        replacement="qcom,msm8998-mmcc",
        since_kernel="4.20",
        description="Correct — no rename needed",
    ),
    "qcom,external-bus-interconnect": _BindingEntry(
        replacement="qcom,sdm845-qnoc",
        since_kernel="5.3",
        description="Vendor generic compatible replaced by per-SoC NoC binding",
    ),
    "qcom,sdm845-cdsp-pil": _BindingEntry(
        replacement="qcom,sdm845-cdsp-pas",
        since_kernel="5.14",
        description="PIL renamed to PAS (Peripheral Authentication Service)",
    ),
    "qcom,smd-rpm": _BindingEntry(
        replacement="qcom,rpm-smd",
        since_kernel="5.7",
        description="RPM SMD compatible reversed in mainline bindings update",
    ),

    # ── NXP i.MX ──────────────────────────────────────────────────────────────

    "fsl,imx8mm-blk-ctrl": _BindingEntry(
        replacement="fsl,imx8mm-media-blk-ctrl",
        since_kernel="5.19",
        description="Block control nodes got subsystem-specific names",
    ),
    "fsl,imx8mp-blk-ctrl": _BindingEntry(
        replacement="fsl,imx8mp-media-blk-ctrl",
        since_kernel="5.19",
        description="Block control nodes got subsystem-specific names",
    ),
    "fsl,imx8-ss-sai": _BindingEntry(
        replacement="fsl,imx8qm-sai",
        since_kernel="5.16",
        description="Generic i.MX8 SAI compatible replaced by per-SoC variant",
    ),
    "fsl,imx-snvs-pwrkey": _BindingEntry(
        replacement="fsl,sec-v4.0-mon-rtc-lp",
        since_kernel="5.10",
        description="SNVS power key binding restructured in mainline",
    ),
    "fsl,imx8mp-audiomix-blk-ctrl": _BindingEntry(
        replacement="fsl,imx8mp-audio-blk-ctrl",
        since_kernel="6.1",
        description="AudioMix block control compatible simplified",
    ),
}


# ── Helper ────────────────────────────────────────────────────────────────────


def _iter_compatibles(ir_node) -> List[str]:
    """Return all compatible strings for an IRNode as a flat list."""
    compat = ir_node.properties.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return [str(c) for c in compat]
    if compat:
        return [str(compat)]
    return []


# ── Rule ──────────────────────────────────────────────────────────────────────


class COMP101DeprecatedVendorBinding(BaseRule):
    """COMP-101: Deprecated vendor BSP binding detected.

    Scans all device nodes for compatible strings that are known to be
    vendor BSP-specific or renamed before/during upstreaming to mainline
    Linux.  Using these strings against a mainline kernel causes binding
    probe failures or missing driver attachment.
    """

    code = "COMP-101"
    name = "Deprecated Vendor BSP Binding"
    description = (
        "A compatible string exists only in a vendor BSP kernel (or was "
        "renamed during mainline upstreaming).  The device will not be "
        "recognised by a mainline kernel using this binding."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, ir_node in model.devices.items():
            for compat in _iter_compatibles(ir_node):
                entry = _DEPRECATED_BINDINGS.get(compat)
                if entry is None:
                    continue

                violations.append(
                    self._create_violation(
                        message=(
                            f"Deprecated vendor BSP binding: "
                            f"{compat!r} (node: {node_name})"
                        ),
                        impact=(
                            f"{entry.description}. "
                            f"On mainline {entry.since_kernel}+ the device "
                            f"will not match any driver."
                        ),
                        suggestion=(
                            f"Replace {compat!r} with "
                            f"{entry.replacement!r} "
                            f"(mainline {entry.since_kernel}+)."
                        ),
                        location=ir_node.path,
                        affected_nodes=[node_name],
                    )
                )

        return violations


def register_compat_rules(registry, soc_name: str = "common") -> None:
    """Register COMP-1xx rules into *registry*."""
    registry.register(COMP101DeprecatedVendorBinding(), soc_name)


__all__ = [
    "COMP101DeprecatedVendorBinding",
    "register_compat_rules",
    "_DEPRECATED_BINDINGS",
]
