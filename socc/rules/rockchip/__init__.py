"""Rockchip rule set initializer."""

from .clock_rules import register_rockchip_clock_rules
from .power_rules import register_rockchip_power_rules
from .gpio_rules import register_rockchip_gpio_rules


def register_rockchip_rules(registry, soc_name: str = "rockchip") -> None:
    """Register all Rockchip rule sets.

    Args:
        registry: Rule registry instance.
        soc_name: SoC name (e.g. "rk3588", "rk3566").

    Note: the bus / interrupt / memory checks that used to live here were
    constraint-driven and SoC-agnostic; they have moved to the ``common`` rule
    set (:func:`socc.rules.common.register_common_rules`) so every vendor
    benefits from them.
    """
    register_rockchip_power_rules(registry, soc_name)
    register_rockchip_clock_rules(registry, soc_name)
    register_rockchip_gpio_rules(registry, soc_name)


__all__ = [
    "register_rockchip_rules",
    "register_rockchip_power_rules",
    "register_rockchip_clock_rules",
    "register_rockchip_gpio_rules",
]
