"""Shared state, SoC name lists, and utility helpers for all command modules."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from socc.rules import RuleRegistry
from socc.engine import Checker
from socc.rules.rockchip import register_rockchip_rules
from socc.rules.allwinner import register_allwinner_rules, ALLWINNER_SOC_NAMES
from socc.rules.amlogic import register_amlogic_rules, AMLOGIC_SOC_NAMES
from socc.rules.qualcomm import register_qualcomm_rules, QUALCOMM_SOC_NAMES
from socc.rules.nxp import register_all_nxp_rules, NXP_SOC_NAMES
from socc.rules.common import register_common_rules
from socc.parser import build_sample_model, parse_dts_file
from socc.config import load_config, filter_by_severity, SAMPLE_CONFIG

# ── SoC name lists ─────────────────────────────────────────────────────────

ROCKCHIP_SOCS = ["rk3588", "rk3576", "rk3568", "rk3566", "rk3399", "rk3328", "rk3528", "rk3308"]
ALLWINNER_SOCS = sorted(ALLWINNER_SOC_NAMES)
AMLOGIC_SOCS = sorted(AMLOGIC_SOC_NAMES)
QUALCOMM_SOCS = sorted(QUALCOMM_SOC_NAMES)
NXP_SOCS = sorted(NXP_SOC_NAMES)
ALL_SOC_CHOICES = ROCKCHIP_SOCS + ALLWINNER_SOCS + AMLOGIC_SOCS + QUALCOMM_SOCS + NXP_SOCS + ["auto"]

# Convenience re-exports used in command modules
__all__ = [
    "ROCKCHIP_SOCS", "ALLWINNER_SOCS", "AMLOGIC_SOCS", "QUALCOMM_SOCS", "NXP_SOCS",
    "ALL_SOC_CHOICES", "build_registry", "auto_detect_soc", "severity_tag", "echo",
    "load_config", "filter_by_severity", "SAMPLE_CONFIG",
    "build_sample_model", "parse_dts_file", "Checker", "Path", "Optional", "click",
]


def build_registry() -> RuleRegistry:
    """Build and return a fully populated rule registry (all vendors)."""
    registry = RuleRegistry()
    register_common_rules(registry)
    for soc in ROCKCHIP_SOCS:
        register_rockchip_rules(registry, soc)
    for soc in ALLWINNER_SOCS:
        register_allwinner_rules(registry, soc)
    for soc in AMLOGIC_SOCS:
        register_amlogic_rules(registry, soc)
    for soc in QUALCOMM_SOCS:
        register_qualcomm_rules(registry, soc)
    for soc in NXP_SOCS:
        register_all_nxp_rules(registry, soc)
    return registry


def auto_detect_soc(filename: str) -> str:
    """Infer SoC family from a DTS filename (lower-cased)."""
    fn = filename.lower()

    for chip in ["rk3588", "rk3576", "rk3568", "rk3566", "rk3399", "rk3328"]:
        if chip in fn:
            return chip

    if "sun50i-h616" in fn or "h616" in fn or "h618" in fn:
        return "sun50i-h616"
    if "sun50i-h6" in fn or "h6-" in fn or "-h6." in fn:
        return "sun50i-h6"
    if "sun50i-a64" in fn or "a64" in fn:
        return "sun50i-a64"
    if "sun8i-h3" in fn or "h3-" in fn or "-h3." in fn:
        return "sun8i-h3"
    if "sun8i-h2" in fn or "h2-plus" in fn:
        return "sun8i-h2-plus"
    if "sun50i-h5" in fn or "h5-" in fn:
        return "sun50i-h5"
    if "sun20i-d1" in fn or "d1-" in fn:
        return "sun20i-d1"
    if "sun55i-a527" in fn or "a527" in fn or "t527" in fn:
        return "sun55i-a527"
    if "sun55i-a733" in fn or "a733" in fn:
        return "sun55i-a733"
    if "sun55i-a523" in fn or "a523" in fn:
        return "sun55i-a523"

    for chip in ["sdm845", "sm8250", "sm8350", "sm8450", "sc7180", "sc7280", "sc8280xp", "qcs6490", "qcs9100", "msm8998"]:
        if chip in fn:
            return chip

    if "gxbb" in fn or "s905." in fn or "s905-" in fn:
        return "meson-gxbb"
    if "gxl" in fn or "s905x" in fn or "s905d" in fn or "s905w" in fn:
        return "meson-gxl"
    if "gxm" in fn or "s912" in fn:
        return "meson-gxm"
    if "g12b" in fn or "s922x" in fn or ("a311d2" not in fn and "a311d" in fn):
        return "meson-g12b"
    if "g12a" in fn or "s905x2" in fn or "s905d2" in fn:
        return "meson-g12a"
    if "sm1" in fn or "s905x3" in fn or "s905d3" in fn or "odroid-c4" in fn:
        return "meson-sm1"
    if "axg" in fn or "a113d" in fn or "a113x" in fn:
        return "meson-axg"
    if "t7" in fn or "a311d2" in fn:
        return "amlogic-t7"

    for chip in ["rk3528", "rk3308"]:
        if chip in fn:
            return chip

    for chip in ["imx8mp", "imx8mq", "imx8mm", "imx8mn", "imx8ulp", "imx93", "imx95"]:
        if chip in fn:
            return chip
    if "imx8m" in fn:
        return "imx8mp"

    return "unknown"


def severity_tag(severity: str, color: Optional[bool]) -> str:
    """Return a colored severity tag for terminal output."""
    tags = {
        "error":   ("[E]", "red",    True),
        "warning": ("[W]", "yellow", False),
        "info":    ("[I]", "cyan",   False),
    }
    label, fg, bold = tags.get(severity, ("[?]", "white", False))
    if color is False:
        return label
    return click.style(label, fg=fg, bold=bold)


def echo(msg: str, color: Optional[bool] = None) -> None:
    """Print a status message."""
    click.echo(msg)
