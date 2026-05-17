"""Qualcomm rule set initializer."""

from .power_rules import (
    QC001RPMhMissing, QC002SPMIMissing, QC003CXRailMissing,
    register_qualcomm_power_rules,
)
from .clock_rules import (
    QC101GCCMissing, QC102InvalidClockFrequency, QC103SubsystemFirmwareMissing,
    register_qualcomm_clock_rules,
)
from .gpio_rules import (
    QC201TLMMControllerMissing, QC202TLMMVoltageConflict, QC203UARTDebugNodeMissing,
    register_qualcomm_gpio_rules,
)


# Qualcomm SoC family names recognized by this rule set.
# Use the "qcom,<soc>" compatible prefix (without the "qcom," part here).
QUALCOMM_SOC_NAMES = {
    "sdm845",     # Snapdragon 845 — DB845c, various phones
    "sm8250",     # Snapdragon 865 — RB5, flagship phones (2020)
    "sm8350",     # Snapdragon 888 — RB5 Gen2
    "sm8450",     # Snapdragon 8 Gen1
    "sc7180",     # SC7180 — ARM Chromebooks (Acer Spin 513, HP x2 11)
    "sc7280",     # SC7280 / Snapdragon 7c Gen2 — Chromebooks
    "sc8280xp",   # Snapdragon 8cx Gen3 — ThinkPad X13s, Surface Pro 9 (5G)
    "qcs6490",    # QCS6490 / Snapdragon 778G industrial — Qualcomm RB3 Gen2
    "qcs9100",    # QCS9100 — edge AI platform (preview)
    "msm8998",    # Snapdragon 835 (legacy, still seen in dev boards)
}


def register_qualcomm_rules(registry, soc_name: str) -> None:
    """Register all Qualcomm validation rules for *soc_name*.

    Args:
        registry: Rule registry instance.
        soc_name: SoC name, e.g. ``"sdm845"`` or ``"sm8250"``.
    """
    register_qualcomm_power_rules(registry, soc_name)
    register_qualcomm_clock_rules(registry, soc_name)
    register_qualcomm_gpio_rules(registry, soc_name)


__all__ = [
    "register_qualcomm_rules",
    "QUALCOMM_SOC_NAMES",
    "QC001RPMhMissing",
    "QC002SPMIMissing",
    "QC003CXRailMissing",
    "QC101GCCMissing",
    "QC102InvalidClockFrequency",
    "QC103SubsystemFirmwareMissing",
    "QC201TLMMControllerMissing",
    "QC202TLMMVoltageConflict",
    "QC203UARTDebugNodeMissing",
]
