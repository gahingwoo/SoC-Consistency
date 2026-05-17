"""Rockchip rule set initializer."""

from .clock_rules import register_rockchip_clock_rules
from .power_rules import register_rockchip_power_rules
from .gpio_rules import register_rockchip_gpio_rules
from .memory_rules import register_rockchip_memory_rules
from .bus_rules import register_rockchip_bus_rules
from .interrupt_rules import register_rockchip_interrupt_rules


def register_rockchip_rules(registry, soc_name: str = "rockchip") -> None:
    """Register all Rockchip rule sets.

    Args:
        registry: Rule registry instance.
        soc_name: SoC name (e.g. "rk3588", "rk3566").
    """
    register_rockchip_power_rules(registry, soc_name)
    register_rockchip_clock_rules(registry, soc_name)
    register_rockchip_gpio_rules(registry, soc_name)
    register_rockchip_memory_rules(registry, soc_name)
    register_rockchip_bus_rules(registry, soc_name)
    register_rockchip_interrupt_rules(registry, soc_name)


__all__ = [
    "register_rockchip_rules",
    "register_rockchip_power_rules",
    "register_rockchip_clock_rules",
    "register_rockchip_gpio_rules",
    "register_rockchip_memory_rules",
    "register_rockchip_bus_rules",
    "register_rockchip_interrupt_rules",
]
