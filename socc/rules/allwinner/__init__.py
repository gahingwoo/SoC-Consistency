"""Allwinner rule set initializer."""

from .clock_rules import AW101CCUClockProviderMissing, AW102InvalidClockFrequency, AW103RCCURequired
from .power_rules import AW001PMICSupplyMissing, AW002RegulatorVoltageOutOfRange, AW003PowerTreeCycle
from .gpio_rules import AW201PioNodeMissing, AW202BankVoltageMismatch, AW203TooManyPinsPerDevice
from .iommu_rules import AW301IommuMasterIdCollision, register_allwinner_iommu_rules


# Allwinner SoC names recognized by this rule set
ALLWINNER_SOC_NAMES = {
    "sun8i-h3",
    "sun8i-h2-plus",
    "sun50i-h5",
    "sun50i-h6",
    "sun50i-h616",
    "sun50i-h618",
    "sun50i-a64",
    "sun55i-a523",
    "sun55i-a527",   # A527/T527 — big.LITTLE A55+A510, NPU
    "sun55i-a733",   # A733 — industrial/automotive sun55i variant
    "sun20i-d1",
}


def register_allwinner_rules(registry, soc_name: str) -> None:
    """Register all Allwinner validation rules for *soc_name*.

    Args:
        registry: Rule registry instance.
        soc_name: SoC family name, e.g. ``"sun50i-h616"`` or ``"sun50i-a64"``.
    """
    # Power domain rules
    registry.register(AW001PMICSupplyMissing(), soc_name)
    registry.register(AW002RegulatorVoltageOutOfRange(), soc_name)
    registry.register(AW003PowerTreeCycle(), soc_name)

    # Clock rules
    registry.register(AW101CCUClockProviderMissing(), soc_name)
    registry.register(AW102InvalidClockFrequency(), soc_name)
    registry.register(AW103RCCURequired(), soc_name)

    # GPIO / pinctrl rules
    registry.register(AW201PioNodeMissing(), soc_name)
    registry.register(AW202BankVoltageMismatch(), soc_name)
    registry.register(AW203TooManyPinsPerDevice(), soc_name)

    # IOMMU rules
    register_allwinner_iommu_rules(registry, soc_name)


__all__ = [
    "register_allwinner_rules",
    "register_allwinner_iommu_rules",
    "AW301IommuMasterIdCollision",
    "ALLWINNER_SOC_NAMES",
]
