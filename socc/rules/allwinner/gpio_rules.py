"""Allwinner pio (pin controller) rules."""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


# Allwinner SoCs use a unified "pio" pinctrl node that covers all GPIO banks
# except the "R_" always-on domain (handled by "r_pio").
# All GPIO banks are sub-nodes of a single pio@base_address node.
# The bank naming is PX where X is a letter (PA, PB, ..., PL).


class AW201PioNodeMissing(BaseRule):
    """AW-201: pio pinctrl node absent."""

    code = "AW-201"
    name = "pio Pinctrl Node Missing"
    description = (
        "Allwinner SoCs require a single 'pio' pinctrl/GPIO controller node "
        "with compatible 'allwinner,sun*-pinctrl'.  Without it, all GPIO and "
        "pinmux requests will fail."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Check if any GPIO node references a controller
        gpio_nodes = [
            name for name in model.devices
            if "gpio" in name.lower() or "pio" in name.lower()
        ]

        if not gpio_nodes and model.devices:
            violations.append(
                self._create_violation(
                    message="No 'pio' or GPIO controller node found in the device tree.",
                    impact=(
                        "All GPIO consumers (regulators, card-detect, reset lines) "
                        "will fail to obtain their GPIO descriptors."
                    ),
                    suggestion=(
                        "Include the SoC-level DTSI which defines the pio node, "
                        "e.g. sun50i-h616.dtsi or sun8i-h3.dtsi."
                    ),
                    location="/soc/pio",
                    affected_nodes=["pio"],
                )
            )

        return violations


class AW202BankVoltageMismatch(BaseRule):
    """AW-202: GPIO bank voltage level mismatch."""

    code = "AW-202"
    name = "GPIO Bank Voltage Mismatch"
    description = (
        "Certain Allwinner GPIO banks operate at 1.8V (e.g., PC on H616, PG on H3/A64). "
        "Driving a 1.8V bank with 3.3V IO supply will damage the SoC."
    )
    severity = "error"

    # Banks known to be 1.8V on Allwinner sun50i (H616/H618/A64)
    _LOW_VOLTAGE_BANKS = {"pc", "pg"}

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, node in model.devices.items():
            name_lower = node_name.lower()
            bank = None
            for b in self._LOW_VOLTAGE_BANKS:
                if b in name_lower:
                    bank = b.upper()
                    break
            if bank is None:
                continue

            voltage = getattr(node, "voltage", None)
            if voltage is not None and voltage > 1.98:
                violations.append(
                    self._create_violation(
                        message=(
                            f"GPIO bank P{bank} is a 1.8V bank but is supplied "
                            f"at {voltage:.1f}V."
                        ),
                        impact=(
                            f"Overvoltage on P{bank} may permanently damage the SoC IO pad. "
                            "Maximum safe voltage for this bank is 1.98V."
                        ),
                        suggestion=(
                            f"Change the VCC supply for P{bank} to a 1.8V regulator output. "
                            "Verify the vcc-pb-supply / vcc-pc-supply phandle in your board DTS."
                        ),
                        location=f"/soc/pio/{bank}",
                        affected_nodes=[node_name],
                    )
                )

        return violations


class AW203TooManyPinsPerDevice(BaseRule):
    """AW-203: Device uses more GPIO pins than its function requires."""

    code = "AW-203"
    name = "Excessive GPIO Pin Assignment"
    description = (
        "A device should not be assigned more GPIO pins than its function requires. "
        "Excess pin claims waste resources and may cause pinmux conflicts."
    )
    severity = "warning"

    # Maximum sensible pin count per device type
    _MAX_PINS = {
        "i2c": 2,
        "spi": 4,
        "uart": 4,
        "mmc": 8,
        "emac": 14,
        "lcd": 28,
    }

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name in model.devices:
            pin_count = model.device_supplies.get(device_name, [])
            # Approximate pin usage from supply count as a proxy
            # (actual pin count would require richer IR model data)
            supply_count = len(pin_count)
            if supply_count == 0:
                continue

            dev_lower = device_name.lower()
            for prefix, max_pins in self._MAX_PINS.items():
                if prefix in dev_lower and supply_count > max_pins:
                    violations.append(
                        self._create_violation(
                            message=(
                                f"Device {device_name!r} has {supply_count} supply entries "
                                f"but {prefix.upper()} peripherals should use at most {max_pins}."
                            ),
                            impact="Excess pin assignments may block other devices from using shared pins.",
                            suggestion=(
                                f"Review the pinctrl configuration for {device_name!r} and "
                                "remove unnecessary pin entries."
                            ),
                            location=f"/{device_name}",
                            affected_nodes=[device_name],
                        )
                    )
                    break

        return violations
