"""NXP rule set initializer."""

from .imx_rules import register_nxp_rules, IMX001ArmPllFreqLimit, IMX002DramBeforeSocCore
from .imx_clock_rules import (
    register_nxp_clock_rules, IMX101CCMControllerMissing, IMX102ReferenceOscMissing,
)
from .imx_power_rules import (
    register_nxp_power_rules, IMX201PmicMissing, IMX202CoreRailMissing,
)
from .imx_gpio_rules import (
    register_nxp_gpio_rules, IMX301IomuxcMissing, IMX302GpioPinOutOfRange,
)

# Recognized NXP SoC names for CLI auto-detection
NXP_SOC_NAMES = [
    "imx8mp",
    "imx8mq",
    "imx8mm",
    "imx8mn",
    "imx8ulp",
    "imx93",
    "imx95",
]


def register_all_nxp_rules(registry, soc_name: str = "imx8mp") -> None:
    """Register all NXP rule sets for *soc_name*."""
    register_nxp_rules(registry, soc_name)
    register_nxp_clock_rules(registry, soc_name)
    register_nxp_power_rules(registry, soc_name)
    register_nxp_gpio_rules(registry, soc_name)


__all__ = [
    "register_nxp_rules",
    "register_all_nxp_rules",
    "register_nxp_clock_rules",
    "register_nxp_power_rules",
    "register_nxp_gpio_rules",
    "IMX001ArmPllFreqLimit",
    "IMX002DramBeforeSocCore",
    "IMX101CCMControllerMissing",
    "IMX102ReferenceOscMissing",
    "IMX201PmicMissing",
    "IMX202CoreRailMissing",
    "IMX301IomuxcMissing",
    "IMX302GpioPinOutOfRange",
    "NXP_SOC_NAMES",
]
