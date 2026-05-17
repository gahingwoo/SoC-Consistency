"""Allwinner CCU (Clock Control Unit) rules."""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


# Allwinner SoCs use a Central Clock Unit (CCU / R_CCU) to supply all
# peripheral clocks.  All clock consumers MUST reference the CCU node;
# referencing a non-existent clock provider is a hard error.


class AW101CCUClockProviderMissing(BaseRule):
    """AW-101: CCU clock provider not referenced by peripheral device."""

    code = "AW-101"
    name = "CCU Clock Provider Missing"
    description = (
        "Every Allwinner peripheral must reference the CCU (or R_CCU) as its "
        "clock provider.  A device with no registered clock provider will fail to probe."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Check that devices which require clocks have at least one registered provider
        for device_name, clocks in model.device_clocks.items():
            if not clocks:
                violations.append(
                    self._create_violation(
                        message=(
                            f"Device {device_name!r} has no clock assigned. "
                            "On Allwinner SoCs every peripheral requires a CCU clock reference."
                        ),
                        impact=f"Driver for {device_name!r} will fail with -ENOENT on clk_get().",
                        suggestion=(
                            "Add a 'clocks' property referencing the CCU phandle and the "
                            "correct clock ID (see allwinner,*-ccu binding documentation)."
                        ),
                        location=f"/{device_name}",
                        affected_nodes=[device_name],
                    )
                )

        return violations


class AW102InvalidClockFrequency(BaseRule):
    """AW-102: Peripheral assigned-clock-rates outside CCU-supported range."""

    code = "AW-102"
    name = "Invalid Clock Frequency"
    description = (
        "assigned-clock-rates values must fall within the valid range supported "
        "by the Allwinner CCU for that peripheral type."
    )
    severity = "warning"

    # Per-type frequency bounds (Hz)
    _BOUNDS = {
        "i2c":  (100_000, 400_000),
        "spi":  (3_000_000, 100_000_000),
        "uart": (1_000_000, 4_000_000),
        "mmc":  (400_000, 52_000_000),
        "emac": (25_000_000, 125_000_000),
    }

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, clocks in model.device_clocks.items():
            for clock in clocks:
                freq = getattr(clock, "frequency", None)
                if freq is None:
                    continue

                dev_lower = device_name.lower()
                for prefix, (lo, hi) in self._BOUNDS.items():
                    if prefix in dev_lower:
                        if not (lo <= freq <= hi):
                            violations.append(
                                self._create_violation(
                                    message=(
                                        f"Device {device_name!r} clock frequency "
                                        f"{freq:,} Hz is outside [{lo:,}, {hi:,}] Hz."
                                    ),
                                    impact="Driver may malfunction or CCU will silently clamp the rate.",
                                    suggestion=(
                                        f"Set assigned-clock-rates within [{lo:,}, {hi:,}] Hz "
                                        f"for {prefix.upper()} peripherals on this Allwinner SoC."
                                    ),
                                    location=f"/{device_name}",
                                    affected_nodes=[device_name],
                                )
                            )
                        break

        return violations


class AW103RCCURequired(BaseRule):
    """AW-103: R_CCU must be present for always-on domain devices."""

    code = "AW-103"
    name = "R_CCU Required for Always-On Domain"
    description = (
        "Allwinner SoCs have an always-on (R_CCU) domain for UART, RSB, and "
        "pinctrl nodes that must remain active in suspend.  These nodes must "
        "reference r_ccu, not the main CCU."
    )
    severity = "warning"

    # Nodes that should reference r_ccu (simplified check on device name prefix)
    _R_CCU_NODES = {"r_uart", "r_i2c", "r_pio", "r_rsb", "r_pwm"}

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, clocks in model.device_clocks.items():
            for prefix in self._R_CCU_NODES:
                if device_name.lower().startswith(prefix) and not clocks:
                    violations.append(
                        self._create_violation(
                            message=(
                                f"Always-on device {device_name!r} has no R_CCU clock. "
                                "Devices in the R_ domain must reference the R_CCU clock provider."
                            ),
                            impact="Device will lose its clock during suspend; system resume may fail.",
                            suggestion=(
                                f"Reference the r_ccu phandle in the 'clocks' property of "
                                f"{device_name!r}."
                            ),
                            location=f"/{device_name}",
                            affected_nodes=[device_name],
                        )
                    )

        return violations
