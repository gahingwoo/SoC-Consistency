"""Qualcomm TLMM (Top Level Mode Multiplexer) / GPIO rules.

Qualcomm SoCs consolidate all GPIO into a single TLMM controller.
Unlike Rockchip (multi-bank) or Allwinner (PIO banks), TLMM exposes
all pins through one node.  Voltage is primarily 1.8V on the core
rails, with some pads configurable via PMIC LDOs.

Common mistakes:
- TLMM node missing entirely
- Drive-strength exceeding 16 mA HDRV limit
- 3.3V signals connected to a 1.8V TLMM pad without a level-shifter
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


class QC201TLMMControllerMissing(BaseRule):
    """QC-201: TLMM GPIO controller node not found."""

    code = "QC-201"
    name = "TLMM GPIO Controller Missing"
    description = (
        "Qualcomm platforms use a single Top Level Mode Multiplexer (TLMM) for "
        "all GPIO and pin control.  Its absence breaks all pinmux configurations."
    )
    severity = "error"

    _TLMM_COMPATIBLES = (
        "qcom,sdm845-tlmm",
        "qcom,sm8250-tlmm",
        "qcom,sc7180-tlmm",
        "qcom,qcs6490-tlmm",
        "qcom,sm8150-tlmm",
        "qcom,sm8350-tlmm",
        "qcom,sm8450-tlmm",
        "qcom,sc8280xp-tlmm",
        "qcom,msm8996-tlmm",
        "qcom,msm8998-tlmm",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        found = any(
            any(compat in str(dev) for compat in self._TLMM_COMPATIBLES)
            for dev in model.devices
        )

        if not found:
            tlmm_in_devices = any(
                "tlmm" in dev.lower() for dev in model.devices
            )
            if not tlmm_in_devices:
                violations.append(
                    self._create_violation(
                        message=(
                            "No Qualcomm TLMM (qcom,*-tlmm) GPIO controller node found. "
                            "All pinmux configurations will be silently ignored by the kernel."
                        ),
                        impact=(
                            "Peripheral pin assignments (UART, I2C, SPI, display, camera) "
                            "will not be applied; devices may probe but behave incorrectly."
                        ),
                        suggestion=(
                            "Add the TLMM controller node:\n"
                            "  tlmm: pinctrl@3400000 {\n"
                            "    compatible = \"qcom,sdm845-tlmm\";\n"
                            "    reg = <0 0x03400000 0 0xc00000>;\n"
                            "    gpio-controller;\n"
                            "    #gpio-cells = <2>;\n"
                            "    interrupt-controller;\n"
                            "    #interrupt-cells = <2>;\n"
                            "  };"
                        ),
                        location="/soc",
                        affected_nodes=["tlmm"],
                    )
                )

        return violations


class QC202TLMMVoltageConflict(BaseRule):
    """QC-202: 3.3V device on 1.8V-default TLMM pad."""

    code = "QC-202"
    name = "TLMM IO Voltage Conflict"
    description = (
        "Qualcomm TLMM pads default to 1.8V IO.  Connecting 3.3V signals without "
        "a level-shifter or a configurable voltage LDO violates the datasheet and "
        "can permanently damage the SoC."
    )
    severity = "error"

    _HIGH_VOLTAGE_SUPPLIES = ("vcc_3v3", "vdd_3v3", "3v3", "vccio_3v3", "vbus_3v3")

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, supplies in model.device_supplies.items():
            # Flag TLMM-adjacent peripherals (SDcard, SPI-NOR) directly wired to 3.3V
            is_gpio_device = any(
                kw in device_name.lower()
                for kw in ("gpio", "pinctrl", "tlmm")
            )
            if not is_gpio_device:
                continue

            for supply in supplies:
                if any(hv in supply.lower() for hv in self._HIGH_VOLTAGE_SUPPLIES):
                    violations.append(
                        self._create_violation(
                            message=(
                                f"GPIO/pinctrl device {device_name!r} references supply "
                                f"{supply!r} (3.3V).  Qualcomm TLMM pads are 1.8V by default."
                            ),
                            impact=(
                                "Operating TLMM pads at 3.3V exceeds the absolute maximum rating "
                                "and can permanently damage the SoC IO cells."
                            ),
                            suggestion=(
                                "Use a 1.8V supply or add a dedicated level-shifter IC. "
                                "Some boards use a configurable LDO (e.g. L11A at 1.8V/3.0V) "
                                "controlled by PMIC to support 3.0V SD card signalling."
                            ),
                            location=f"/{device_name}",
                            affected_nodes=[device_name, supply],
                        )
                    )

        return violations


class QC203UARTDebugNodeMissing(BaseRule):
    """QC-203: Debug UART (GENI-SE or BAM UART) not declared."""

    code = "QC-203"
    name = "Debug UART Node Missing"
    description = (
        "Qualcomm boards typically expose a debug UART via the GENI Serial Engine (GSI). "
        "Without the uart node and chosen/stdout-path, early boot messages are invisible."
    )
    severity = "info"

    _UART_COMPATIBLES = (
        "qcom,geni-uart",
        "qcom,msm-uartdm",
        "snps,dwc3",  # allow USB debug as fallback
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        has_uart = any(
            any(compat in str(dev) for compat in self._UART_COMPATIBLES)
            for dev in model.devices
        )
        if not has_uart:
            has_uart_by_name = any("uart" in dev.lower() for dev in model.devices)

        if not has_uart and not any("uart" in dev.lower() for dev in model.devices):
            violations.append(
                self._create_violation(
                    message=(
                        "No UART debug node (qcom,geni-uart or qcom,msm-uartdm) found. "
                        "Early boot console output is unavailable."
                    ),
                    impact="Boot failures are silent; kernel crashes and driver errors cannot be diagnosed.",
                    suggestion=(
                        "Add a UART node and set chosen/stdout-path:\n"
                        "  uart@a90000 {\n"
                        "    compatible = \"qcom,geni-uart\";\n"
                        "    reg = <0 0x00a90000 0 0x4000>;\n"
                        "    status = \"okay\";\n"
                        "  };\n"
                        "  chosen { stdout-path = \"uart@a90000:115200n8\"; };"
                    ),
                    location="/soc",
                    affected_nodes=["uart"],
                )
            )

        return violations


def register_qualcomm_gpio_rules(registry, soc_name: str) -> None:
    """Register Qualcomm GPIO/TLMM rules."""
    registry.register(QC201TLMMControllerMissing(), soc_name)
    registry.register(QC202TLMMVoltageConflict(), soc_name)
    registry.register(QC203UARTDebugNodeMissing(), soc_name)
