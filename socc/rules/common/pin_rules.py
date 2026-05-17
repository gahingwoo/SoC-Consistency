"""Peripheral pin-routing rules (PIN-3xx series).

PIN-301  Phantom Peripheral — bus interface enabled but missing pinctrl routing

A bus controller (I2C, SPI, UART, PWM, CAN) can be fully initialised by the
kernel driver and show as probed in dmesg, while its signal lines are never
actually routed to physical pads because the DTS node lacks a ``pinctrl-0``
binding.  The peripheral appears functional from the kernel side but
is electrically disconnected from the board.

This is one of the most time-consuming DTS bugs to diagnose because:
  - The driver loads without errors.
  - /dev/i2cN (or /dev/ttyN, etc.) appears.
  - i2cdetect / logic-analyser trace shows no activity on the physical pins.
  - GPIO pad is still in its reset-state function (usually GPIO input with
    no pull), which may look like a floating line to external hardware.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Peripheral classification ─────────────────────────────────────────────────

# Bus type keywords to look for in the node name or compatible string.
# Value: human-readable description of the required signal lines.
_BUS_REQUIRED_SIGNALS: Dict[str, Tuple[str, ...]] = {
    "i2c":    ("SDA", "SCL"),
    "spi":    ("MOSI/MISO", "CLK", "CS"),
    "uart":   ("TX", "RX"),
    "serial": ("TX", "RX"),
    "usart":  ("TX", "RX"),
    "pwm":    ("PWM output",),
    "can":    ("CANBUS TX", "CANBUS RX"),
    "sdmmc":  ("CLK", "CMD", "DAT0"),
    "usdhc":  ("CLK", "CMD", "DAT0"),
    "sdhci":  ("CLK", "CMD", "DAT0"),
}

# Compatible-string keywords that identify a bus master controller node.
# Ordered longest → shortest so that e.g. "snps,dw-apb-uart" matches "uart"
# before the generic "spi" would match "spi-gpio".
_BUS_COMPAT_KEYWORDS = [
    "dw-apb-uart", "ns16550", "uart", "usart",
    "spi-nor",
    "i2c",
    "spi",
    "pwm",
    "can",
    "snps,dw-mshc", "sdmmc", "usdhc", "sdhci",
    "serial",
]


def _classify_bus(node_name: str, compatible: str) -> str | None:
    """Return a bus type keyword or None if the node is not a bus controller."""
    text = (node_name + " " + compatible).lower()
    for kw in _BUS_COMPAT_KEYWORDS:
        if kw in text:
            # Normalise to the canonical key used in _BUS_REQUIRED_SIGNALS
            canonical = kw.split(",")[-1].split("-")[0]   # e.g. "uart"
            # Return whichever key from _BUS_REQUIRED_SIGNALS matches
            for bus_key in _BUS_REQUIRED_SIGNALS:
                if bus_key in kw or bus_key == canonical:
                    return bus_key
    return None


# ── Rule class ────────────────────────────────────────────────────────────────


class PIN301PhantomPinmux(BaseRule):
    """PIN-301: Bus interface enabled but ``pinctrl-0`` is absent.

    Checks every device node that has ``status = "okay"`` and whose name or
    compatible string identifies it as a communication bus controller.  A
    missing ``pinctrl-0`` property means the driver will probe successfully
    but its signal lines will never be routed to physical pads — the classic
    phantom-peripheral failure.
    """

    code = "PIN-301"
    name = "Phantom Peripheral — Missing Pinctrl Routing"
    description = (
        "A bus interface (I2C, SPI, UART, PWM, CAN, SD/MMC) is enabled "
        "(status = \"okay\") but has no pinctrl-0 binding.  The driver "
        "probes, /dev/* appears, but the physical pads remain in their "
        "default GPIO state — the peripheral is electrically disconnected "
        "from the board."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, ir_node in model.devices.items():
            props = ir_node.properties

            # Only active (enabled) nodes
            status = props.get("status", "")
            if status not in ("okay", "ok"):
                continue

            # Determine compatible string
            compatible = props.get("compatible", "")
            if isinstance(compatible, list):
                compatible = " ".join(str(c) for c in compatible)
            compatible_str = str(compatible)

            bus_type = _classify_bus(node_name, compatible_str)
            if bus_type is None:
                continue

            # Check for pinctrl binding
            has_pinctrl = (
                "pinctrl-0" in props
                or "pinctrl-names" in props
            )
            if has_pinctrl:
                continue   # binding present — pass

            required = ", ".join(_BUS_REQUIRED_SIGNALS.get(bus_type, ("signal lines",)))
            short_compat = (
                compatible_str.split(",")[-1].strip()
                if "," in compatible_str
                else compatible_str
            )

            violations.append(
                self._create_violation(
                    message=(
                        f"Phantom peripheral: {node_name} ({short_compat}) is "
                        f"enabled but has no pinctrl-0 binding."
                    ),
                    impact=(
                        f"The {bus_type.upper()} controller probes successfully "
                        f"and appears in /dev, but {required} are never routed "
                        f"to physical pads.  External devices will see no "
                        f"activity — the peripheral is electrically disconnected."
                    ),
                    suggestion=(
                        f"Add pinctrl-names and pinctrl-0 to the {node_name} "
                        f"node, referencing a pinctrl group that configures "
                        f"{required} on the correct pads:\n"
                        f"\n"
                        f"    pinctrl-names = \"default\";\n"
                        f"    pinctrl-0 = <&{node_name}m0_xfer>;"
                    ),
                    location=ir_node.path,
                    affected_nodes=[node_name],
                )
            )

        return violations


def register_pin_rules(registry, soc_name: str = "common") -> None:
    """Register PIN-3xx rules into *registry*."""
    registry.register(PIN301PhantomPinmux(), soc_name)


__all__ = [
    "PIN301PhantomPinmux",
    "register_pin_rules",
]
