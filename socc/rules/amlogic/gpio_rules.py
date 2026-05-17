"""Amlogic Meson GPIO / pinctrl rules.

Amlogic SoCs use two pinctrl nodes:
- periphs-pinctrl  (EE domain, compatible: amlogic,meson-*-periphs-pinctrl)
- aobus-pinctrl    (AO domain, compatible: amlogic,meson-*-aobus-pinctrl)

AO-domain GPIO (GPIOAO bank) must reference aobus-pinctrl; all other banks
use periphs-pinctrl.  Additionally some banks are 1.8V and must not be
driven at 3.3V.
"""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


class ML201GPIOControllerMissing(BaseRule):
    """ML-201: GPIO controller node absent."""

    code = "ML-201"
    name = "GPIO Controller Missing"
    description = (
        "Amlogic SoCs require both a 'periphs-pinctrl' node (EE domain) and an "
        "'aobus-pinctrl' node (AO domain).  Absence of either means GPIO consumers "
        "in that domain will fail to obtain descriptors."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        gpio_nodes = [
            n for n in model.devices
            if "gpio" in n.lower() or "pinctrl" in n.lower() or "pio" in n.lower()
        ]

        if not gpio_nodes and model.devices:
            violations.append(
                self._create_violation(
                    message="No GPIO/pinctrl controller node found in the device tree.",
                    impact=(
                        "All GPIO consumers will fail to probe; "
                        "card-detect, reset, and enable GPIOs will be unavailable."
                    ),
                    suggestion=(
                        "Include the SoC-level DTSI that defines periphs-pinctrl "
                        "and aobus-pinctrl, e.g. meson-gxbb.dtsi."
                    ),
                    location="/soc/pinctrl",
                    affected_nodes=["periphs-pinctrl", "aobus-pinctrl"],
                )
            )

        return violations


class ML202GPIOBankVoltageMismatch(BaseRule):
    """ML-202: 1.8V GPIO bank driven at 3.3V."""

    code = "ML-202"
    name = "GPIO Bank Voltage Mismatch"
    description = (
        "Amlogic GPIO banks GPIOC and GPIOA operate at 1.8V. "
        "Applying a 3.3V supply to these banks will permanently damage the SoC."
    )
    severity = "error"

    _LOW_VOLTAGE_BANKS = {"gpioc", "gpioa"}

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, node in model.devices.items():
            name_lower = node_name.lower()
            matched_bank = None
            for bank in self._LOW_VOLTAGE_BANKS:
                if bank in name_lower:
                    matched_bank = bank.upper()
                    break
            if matched_bank is None:
                continue

            voltage = getattr(node, "voltage", None)
            if voltage is not None and voltage > 1.98:
                violations.append(
                    self._create_violation(
                        message=(
                            f"GPIO bank {matched_bank} is a 1.8V bank but is supplied "
                            f"at {voltage:.1f}V."
                        ),
                        impact=(
                            f"Overvoltage on {matched_bank} will cause permanent IO pad damage. "
                            "Maximum safe voltage is 1.98V."
                        ),
                        suggestion=(
                            f"Change the VDDIO supply for {matched_bank} to a 1.8V output. "
                            "Verify the vcc-supply phandle in the pinctrl node."
                        ),
                        location=f"/soc/pinctrl/{matched_bank}",
                        affected_nodes=[node_name],
                    )
                )

        return violations


class ML203AOPinctrlCrossing(BaseRule):
    """ML-203: GPIOAO pin referenced via periphs-pinctrl instead of aobus-pinctrl."""

    code = "ML-203"
    name = "AO GPIO Pinctrl Crossing"
    description = (
        "GPIOAO bank pins must be configured via the aobus-pinctrl node. "
        "Referencing them through periphs-pinctrl will silently fail and the "
        "pin state will be undefined after system suspend."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, node in model.devices.items():
            name_lower = node_name.lower()
            if "gpioao" not in name_lower and "ao_gpio" not in name_lower:
                continue

            pinctrl = getattr(node, "pinctrl_provider", "")
            if pinctrl and "ao" not in pinctrl.lower():
                violations.append(
                    self._create_violation(
                        message=(
                            f"GPIOAO node {node_name!r} references pinctrl provider "
                            f"{pinctrl!r} instead of aobus-pinctrl."
                        ),
                        impact=(
                            "After suspend the periphs domain is powered down; "
                            "GPIOAO pin state will be reset unexpectedly."
                        ),
                        suggestion=(
                            "Change the pinctrl-0 phandle for this node to reference "
                            "the aobus-pinctrl node."
                        ),
                        location=f"/{node_name}",
                        affected_nodes=[node_name],
                    )
                )

        return violations
