"""NXP i.MX clock rules (IMX-1xx series).

The i.MX 8M / 9 families derive every internal clock from a single 24 MHz
reference oscillator routed through the Clock Control Module (CCM).  Two
mistakes break the clock tree before the kernel even reaches DRAM init:

IMX-101  CCM clock controller node missing
    Without ``fsl,imx*-ccm`` no peripheral can obtain a clock; every driver
    that calls ``clk_get()`` fails to probe.

IMX-102  24 MHz reference oscillator missing
    The CCM PLLs lock against the external 24 MHz crystal.  If the root
    oscillator is absent the PLL reference is undefined and clock rates are
    nonsensical.
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


def _compat_of(node) -> str:
    val = getattr(node, "properties", {}).get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


class IMX101CCMControllerMissing(BaseRule):
    """IMX-101: CCM (Clock Control Module) provider node missing."""

    code = "IMX-101"
    name = "CCM Clock Controller Missing (i.MX)"
    description = (
        "The i.MX Clock Control Module (CCM) is the single root clock provider. "
        "Without an fsl,imx*-ccm node every peripheral clock is unavailable and "
        "drivers fail to probe."
    )
    severity = "error"

    _CCM_TOKENS = (
        "fsl,imx8mp-ccm", "fsl,imx8mq-ccm", "fsl,imx8mm-ccm", "fsl,imx8mn-ccm",
        "fsl,imx93-ccm", "fsl,imx95-ccm", "fsl,imx8ulp-cgc1", "imx-ccm", "-ccm",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # A CCM may surface either as a device node or as a registered clock
        # provider in the clock tree.
        found = any(
            any(tok in _compat_of(node) for tok in self._CCM_TOKENS)
            or "ccm" in name.lower()
            for name, node in model.devices.items()
        )
        if not found:
            found = any("ccm" in p.lower() for p in model.clock_tree.providers)

        if not found:
            violations.append(
                self._create_violation(
                    message=(
                        "No i.MX CCM clock controller (fsl,imx*-ccm) found. "
                        "The root clock provider is absent from the device tree."
                    ),
                    impact=(
                        "Every peripheral that requests a clock fails to probe; "
                        "the board hangs early in boot with -EPROBE_DEFER storms."
                    ),
                    suggestion=(
                        "Add the CCM node, e.g.:\n"
                        "  clk: clock-controller@30380000 {\n"
                        "      compatible = \"fsl,imx8mp-ccm\";\n"
                        "      #clock-cells = <1>;\n"
                        "      clocks = <&osc_24m>;\n"
                        "  };"
                    ),
                    location="/soc",
                    affected_nodes=["ccm"],
                )
            )

        return violations


class IMX102ReferenceOscMissing(BaseRule):
    """IMX-102: 24 MHz reference oscillator missing."""

    code = "IMX-102"
    name = "24 MHz Reference Oscillator Missing (i.MX)"
    description = (
        "The CCM PLLs lock against the external 24 MHz crystal (osc_24m). "
        "If the root oscillator is absent every derived clock rate is undefined."
    )
    severity = "warning"

    _OSC_NAME_TOKENS = ("osc_24m", "osc-24m", "clk_24m", "clk-ext", "clock-osc")

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Look for a 24 MHz fixed-clock either by name or by clock-frequency.
        def _is_24m(name, node) -> bool:
            if any(tok in name.lower() for tok in self._OSC_NAME_TOKENS):
                return True
            freq = node.properties.get("clock-frequency")
            if isinstance(freq, (list, tuple)) and freq:
                freq = freq[0]
            try:
                return int(freq) == 24_000_000
            except (TypeError, ValueError):
                return False

        found = any(_is_24m(name, node) for name, node in model.devices.items())
        if not found:
            found = any(
                any(tok in c.lower() for tok in self._OSC_NAME_TOKENS)
                for c in model.clock_tree.clocks
            )

        if not found:
            violations.append(
                self._create_violation(
                    message=(
                        "No 24 MHz reference oscillator (osc_24m) found.  "
                        "The CCM PLL reference clock is undefined."
                    ),
                    impact=(
                        "PLL output frequencies cannot be computed; UART baud "
                        "rates, DRAM timing, and all derived clocks will be wrong."
                    ),
                    suggestion=(
                        "Add the fixed oscillator and feed it to the CCM:\n"
                        "  osc_24m: clock-osc-24m {\n"
                        "      compatible = \"fixed-clock\";\n"
                        "      #clock-cells = <0>;\n"
                        "      clock-frequency = <24000000>;\n"
                        "  };"
                    ),
                    location="/",
                    affected_nodes=["osc_24m"],
                )
            )

        return violations


def register_nxp_clock_rules(registry, soc_name: str) -> None:
    """Register NXP i.MX clock rules."""
    registry.register(IMX101CCMControllerMissing(), soc_name)
    registry.register(IMX102ReferenceOscMissing(), soc_name)
