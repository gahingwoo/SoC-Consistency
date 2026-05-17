"""Amlogic Meson clock controller rules.

Amlogic SoCs use two separate clock controllers:
- Main CLKC  (compatible: amlogic,meson-gxbb-clkc / g12a-clkc / sm1-clkc)
- AO CLKC    (compatible: amlogic,meson-gxbb-aoclkc / g12a-aoclkc)

AO-domain devices must reference the AO clock controller; all others use the
main CLKC.  Mixing the two is a hard error and causes incorrect suspend behavior.
"""

from typing import List

from socc.model import SoC, Violation

from ..base import BaseRule, CheckContext


class ML101CLKCMissing(BaseRule):
    """ML-101: Device clock not assigned."""

    code = "ML-101"
    name = "Clock Not Assigned"
    description = (
        "Every Amlogic peripheral must reference the main CLKC or AO CLKC as "
        "its clock provider.  A missing clock reference causes -ENOENT on probe."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, clocks in model.device_clocks.items():
            if not clocks:
                violations.append(
                    self._create_violation(
                        message=(
                            f"Device {device_name!r} has no clock assignment. "
                            "Amlogic peripherals require a CLKC or AO-CLKC clock reference."
                        ),
                        impact=f"Driver {device_name!r} will fail with -ENOENT on clk_get().",
                        suggestion=(
                            "Add a 'clocks' property pointing to the meson CLKC phandle "
                            "and the appropriate clock ID from dt-bindings/clock/meson*.h."
                        ),
                        location=f"/{device_name}",
                        affected_nodes=[device_name],
                    )
                )

        return violations


class ML102InvalidClockFrequency(BaseRule):
    """ML-102: Peripheral assigned-clock-rates out of supported range."""

    code = "ML-102"
    name = "Clock Frequency Out of Range"
    description = (
        "assigned-clock-rates for Amlogic peripherals must fall within the "
        "range that the CLKC supports for that peripheral type."
    )
    severity = "warning"

    _BOUNDS = {
        "i2c":  (100_000, 400_000),
        "spi":  (1_000_000, 100_000_000),
        "uart": (1_000_000, 4_000_000),
        "mmc":  (400_000, 52_000_000),
        "eth":  (25_000_000, 125_000_000),
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
                                        f"Device {device_name!r} clock {freq:,} Hz "
                                        f"is outside valid range [{lo:,}, {hi:,}] Hz."
                                    ),
                                    impact="CLKC may silently clamp or reject the requested rate.",
                                    suggestion=(
                                        f"Set assigned-clock-rates to a value within "
                                        f"[{lo:,}, {hi:,}] Hz for {prefix.upper()} on this SoC."
                                    ),
                                    location=f"/{device_name}",
                                    affected_nodes=[device_name],
                                )
                            )
                        break

        return violations


class ML103AOClockCrossing(BaseRule):
    """ML-103: AO-domain device references main CLKC instead of AO CLKC."""

    code = "ML-103"
    name = "AO Domain Clock Crossing"
    description = (
        "Devices in the Amlogic AO (Always-On) domain must reference the "
        "ao_clkc, not the main clkc.  Using the main CLKC for AO devices "
        "causes those devices to lose their clock during suspend."
    )
    severity = "error"

    _AO_DEVICE_PREFIXES = ("ao_", "uart_ao", "i2c_ao", "ir_")

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for device_name, clocks in model.device_clocks.items():
            is_ao = any(device_name.lower().startswith(p) for p in self._AO_DEVICE_PREFIXES)
            if not is_ao:
                continue

            for clock in clocks:
                provider = getattr(clock, "provider", "")
                if provider and "ao" not in provider.lower():
                    violations.append(
                        self._create_violation(
                            message=(
                                f"AO-domain device {device_name!r} uses clock provider "
                                f"{provider!r} instead of ao_clkc."
                            ),
                            impact=(
                                "Main CLKC is gated during suspend; "
                                f"{device_name!r} will lose its clock and may panic the kernel."
                            ),
                            suggestion=(
                                f"Change the 'clocks' phandle for {device_name!r} to "
                                "reference the ao_clkc node."
                            ),
                            location=f"/{device_name}",
                            affected_nodes=[device_name],
                        )
                    )

        return violations
