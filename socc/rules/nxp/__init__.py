"""NXP rule set initializer."""

from .imx_rules import register_nxp_rules, IMX001ArmPllFreqLimit, IMX002DramBeforeSocCore

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


__all__ = [
    "register_nxp_rules",
    "register_all_nxp_rules",
    "IMX001ArmPllFreqLimit",
    "IMX002DramBeforeSocCore",
    "NXP_SOC_NAMES",
]
