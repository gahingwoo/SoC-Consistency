"""Amlogic Meson rule set initializer."""

from .power_rules import ML001AOEEDomainMismatch, ML002MissingVDDAO, ML003PowerTreeCycle
from .clock_rules import ML101CLKCMissing, ML102InvalidClockFrequency, ML103AOClockCrossing
from .gpio_rules import ML201GPIOControllerMissing, ML202GPIOBankVoltageMismatch, ML203AOPinctrlCrossing
from .sec_rules import ML301SecureMonitorMissing, register_amlogic_sec_rules


# Amlogic SoC family names recognized by this rule set
AMLOGIC_SOC_NAMES = {
    "meson-gxbb",     # S905
    "meson-gxl",      # S905X/D/W
    "meson-gxm",      # S912
    "meson-axg",      # A113D/A113X
    "meson-g12a",     # S905X2/D2/Y2
    "meson-g12b",     # S922X / A311D
    "meson-sm1",      # S905X3/D3/Y3
    "meson-sc2",      # S905X4/W2 — new-gen A55
    "amlogic-t7",     # A311D2 (T7)
    "amlogic-s4",     # S905Y4
    "amlogic-a1",     # A113L
    "amlogic-c3",
    "amlogic-a4",
    "amlogic-a5",
}


def register_amlogic_rules(registry, soc_name: str) -> None:
    """Register all Amlogic Meson validation rules for *soc_name*.

    Args:
        registry: Rule registry instance.
        soc_name: SoC family name, e.g. ``"meson-gxbb"`` or ``"meson-g12b"``.
    """
    # Power domain rules
    registry.register(ML001AOEEDomainMismatch(), soc_name)
    registry.register(ML002MissingVDDAO(), soc_name)
    registry.register(ML003PowerTreeCycle(), soc_name)

    # Clock rules
    registry.register(ML101CLKCMissing(), soc_name)
    registry.register(ML102InvalidClockFrequency(), soc_name)
    registry.register(ML103AOClockCrossing(), soc_name)

    # GPIO / pinctrl rules
    registry.register(ML201GPIOControllerMissing(), soc_name)
    registry.register(ML202GPIOBankVoltageMismatch(), soc_name)
    registry.register(ML203AOPinctrlCrossing(), soc_name)

    # Security rules
    register_amlogic_sec_rules(registry, soc_name)


__all__ = [
    "register_amlogic_rules",
    "register_amlogic_sec_rules",
    "ML301SecureMonitorMissing",
    "AMLOGIC_SOC_NAMES",
]
