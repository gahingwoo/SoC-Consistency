"""NXP i.MX GPIO / pinmux rules (IMX-3xx series).

On i.MX 8M / 9 the IOMUXC block owns every pad mux and pad-control setting,
and GPIO is exposed through fixed 32-bit banks (gpio1 … gpioN).

IMX-301  IOMUXC pin controller missing
    Without fsl,imx*-iomuxc no ``pinctrl-0`` group can be applied; every pin
    keeps its power-on default and most peripherals are unroutable.

IMX-302  GPIO pin index out of range
    Each i.MX GPIO bank has exactly 32 lines (0-31).  A ``gpio_allocation``
    entry referencing pin >= 32 points at a nonexistent line and panics the
    driver at probe.
"""

from typing import Dict, List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


def _compat_of(node) -> str:
    val = getattr(node, "properties", {}).get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


class IMX301IomuxcMissing(BaseRule):
    """IMX-301: IOMUXC pin controller node missing."""

    code = "IMX-301"
    name = "IOMUXC Pin Controller Missing (i.MX)"
    description = (
        "The i.MX IOMUXC block provides all pad mux and pad-control settings. "
        "Without it no pinctrl group can be applied and peripherals are unroutable."
    )
    severity = "error"

    _IOMUXC_TOKENS = (
        "fsl,imx8mp-iomuxc", "fsl,imx8mq-iomuxc", "fsl,imx8mm-iomuxc",
        "fsl,imx8mn-iomuxc", "fsl,imx93-iomuxc", "fsl,imx95-iomuxc",
        "fsl,imx8ulp-iomuxc", "imx-iomuxc", "-iomuxc",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        found = any(
            any(tok in _compat_of(node) for tok in self._IOMUXC_TOKENS)
            or "iomuxc" in name.lower()
            for name, node in model.devices.items()
        )

        if not found:
            violations.append(
                self._create_violation(
                    message=(
                        "No IOMUXC pin controller (fsl,imx*-iomuxc) found.  "
                        "Pin multiplexing cannot be configured."
                    ),
                    impact=(
                        "Every pinctrl-0 reference is unresolved; UART, I2C, SDHC "
                        "and Ethernet pads keep their reset defaults and the "
                        "corresponding peripherals do not work."
                    ),
                    suggestion=(
                        "Add the IOMUXC node, e.g.:\n"
                        "  iomuxc: pinctrl@30330000 {\n"
                        "      compatible = \"fsl,imx8mp-iomuxc\";\n"
                        "      reg = <0x30330000 0x10000>;\n"
                        "  };"
                    ),
                    location="/soc",
                    affected_nodes=["iomuxc"],
                )
            )

        return violations


class IMX302GpioPinOutOfRange(BaseRule):
    """IMX-302: GPIO pin index exceeds the 32-line bank width."""

    code = "IMX-302"
    name = "GPIO Pin Index Out of Range (i.MX)"
    description = (
        "i.MX GPIO banks are 32 bits wide; valid pin indices are 0-31.  A "
        "reference to pin 32 or higher addresses a line that does not exist."
    )
    severity = "error"

    _BANK_WIDTH = 32

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """Validate GPIO pin indices from constraint metadata.

        Constraint format:
            {
                "gpio_allocation": {
                    "uart1_rts": {"bank": "gpio5", "pin": 10},
                    "led_status": {"bank": "gpio3", "pin": 40}
                }
            }
        """
        violations: List[Violation] = []

        constraints = context.metadata.get("constraints", {})
        gpio_alloc: Dict[str, dict] = constraints.get("gpio_allocation", {})
        if not gpio_alloc:
            return violations

        for signal, cfg in gpio_alloc.items():
            pin = cfg.get("pin", -1)
            bank = cfg.get("bank", "gpio?")
            if isinstance(pin, int) and pin >= self._BANK_WIDTH:
                violations.append(
                    self._create_violation(
                        message=(
                            f"GPIO signal {signal!r} uses {bank} pin {pin}, "
                            f"but i.MX banks have only {self._BANK_WIDTH} lines "
                            f"(0-{self._BANK_WIDTH - 1})."
                        ),
                        impact=(
                            "The driver dereferences a nonexistent GPIO line and "
                            "panics at probe, or silently controls the wrong pad."
                        ),
                        suggestion=(
                            f"Use a pin index in 0-{self._BANK_WIDTH - 1}, or move "
                            f"the signal to the correct bank if it belongs to a "
                            f"different GPIO controller."
                        ),
                        location=f"/{bank}",
                        affected_nodes=[signal, bank],
                    )
                )

        return violations


def register_nxp_gpio_rules(registry, soc_name: str) -> None:
    """Register NXP i.MX GPIO / pinmux rules."""
    registry.register(IMX301IomuxcMissing(), soc_name)
    registry.register(IMX302GpioPinOutOfRange(), soc_name)
