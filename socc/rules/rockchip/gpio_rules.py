"""Rockchip GPIO rules."""

from typing import List, Dict, Set
from socc.model import SoC
from socc.rules.base import BaseRule, Violation, CheckContext


class PIN201DuplicateDefinition(BaseRule):
    """PIN-201: GPIO Pin Duplicate Definition"""

    code = "PIN-201"
    name = "Duplicate Pin Definition"
    description = "A GPIO pin must not be claimed by more than one device."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Check for GPIO pins claimed by more than one device."""
        violations: List[Violation] = []
        
        # count usage per pin
        pin_definitions: Dict[str, List[str]] = {}
        
        # walk device list
        if hasattr(model, "devices") and model.devices:
            for device_name, device in model.devices.items():
                if hasattr(device, "gpio_pins"):
                    for pin_name in device.gpio_pins:
                        if pin_name not in pin_definitions:
                            pin_definitions[pin_name] = []
                        pin_definitions[pin_name].append(device_name)
        
        # report pins claimed by multiple devices
        for pin_name, devices in pin_definitions.items():
            if len(devices) > 1:
                violations.append(
                    self._create_violation(
                        message=f"GPIO pin {pin_name!r} is claimed by multiple devices: {', '.join(devices)}.",
                        impact="Hardware conflict; multiple drivers may short-circuit or corrupt logic.",
                        suggestion="Reassign GPIO pins so each device has a unique pin.",
                        location=f"/gpio/{pin_name}",
                        affected_nodes=[pin_name] + devices,
                    )
                )
        
        return violations


class PIN202VoltageMismatch(BaseRule):
    """PIN-202: GPIO Voltage Level Mismatch"""

    code = "PIN-202"
    name = "Voltage Mismatch"
    description = "GPIO pin voltage must be compatible with the connected device."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check GPIO pin voltage compatibility with connected devices.

        Constraint format:
        {
            "gpio_voltage": {
                "gpio1_*": 3.3,  # GPIO1 bank operates at 3.3V
                "gpio2_*": 1.8   # GPIO2 bank operates at 1.8V
            },
            "device_voltage": {
                "touchscreen": 3.3,
                "sensor": 1.8
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        gpio_voltage = constraints.get("gpio_voltage", {})
        device_voltage = constraints.get("device_voltage", {})
        
        if not (gpio_voltage and device_voltage):
            return violations
        
        # check GPIO connections per device
        if hasattr(model, "devices") and model.devices:
            for device_name, device in model.devices.items():
                device_volt = device_voltage.get(device_name)
                
                if device_volt is None or not hasattr(device, "gpio_pins"):
                    continue
                
                for pin_name in device.gpio_pins:
                    # look up voltage for the GPIO group (e.g. gpio1, gpio2)
                    pin_group = pin_name.split("_")[0]  # e.g. "gpio1_pa5" -> "gpio1"
                    
                    # find group voltage
                    pin_volt = None
                    for gpio_pattern, volt in gpio_voltage.items():
                        if gpio_pattern.replace("_*", "") == pin_group:
                            pin_volt = volt
                            break
                    
                    if pin_volt is not None and pin_volt != device_volt:
                        violations.append(
                            self._create_violation(
                                message=f"Device {device_name!r} ({device_volt}V) is connected "
                                        f"to GPIO pin {pin_name!r} ({pin_volt}V): voltage mismatch.",
                                impact="ESD damage or logic incompatibility; communication will fail.",
                                suggestion="Add a level-shifter or reassign to a pin group at the correct voltage.",
                                location=f"/gpio/{pin_name}",
                                affected_nodes=[device_name, pin_name],
                            )
                        )
        
        return violations


def register_rockchip_gpio_rules(registry, soc_name: str) -> None:
    """Register Rockchip GPIO rules."""
    registry.register(PIN201DuplicateDefinition(), soc_name)
    registry.register(PIN202VoltageMismatch(), soc_name)
