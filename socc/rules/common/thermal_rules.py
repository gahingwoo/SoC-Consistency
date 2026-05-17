"""Thermal management validation rules (THM series).

These rules verify that the thermal zones, trip points, and cooling-device
bindings described in a Device Tree are physically safe and correctly linked.

Rules
─────
THM-001  Critical trip temperature exceeds Tj_MAX for the SoC
THM-002  Thermal zone has no critical trip point (unsafe – no last resort shutdown)
THM-003  Passive cooling zone has polling-delay-passive = 0 (throttling never fires)
"""

from __future__ import annotations

from typing import Dict, List

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Known Tj_MAX database (millicelsius) ──────────────────────────────────────
# Conservative datasheet values.  Rule uses _default for unrecognised SoCs.
_TJ_MAX_MC: Dict[str, int] = {
    # Rockchip
    "rk3588":       125_000,
    "rk3588s":      125_000,
    "rk3576":       125_000,
    "rk3568":       125_000,
    "rk3566":       125_000,
    "rk3399":       125_000,
    "rk3328":       125_000,
    "rk3528":       125_000,
    # NXP i.MX8M family
    "imx8mp":       105_000,
    "imx8mq":       105_000,
    "imx8mm":       105_000,
    "imx8mn":       105_000,
    "imx8ulp":      105_000,
    "imx93":        105_000,
    # Allwinner
    "sun50i-h616":  110_000,
    "sun50i-h618":  110_000,
    "sun50i-h6":    110_000,
    "sun50i-a64":   125_000,
    "sun8i-h3":     125_000,
    # Amlogic
    "meson-g12b":   125_000,
    "meson-g12a":   125_000,
    "meson-sm1":    125_000,
    "meson-gxbb":   125_000,
    "meson-gxl":    125_000,
    # Qualcomm
    "sdm845":       125_000,
    "sm8250":       125_000,
    "sm8350":       125_000,
    "sm8450":       125_000,
    "sc7180":       125_000,
    "sc7280":       125_000,
    # Fallback
    "_default":     125_000,
}


def _tj_max(soc_name: str) -> int:
    """Return the Tj_MAX (millicelsius) for *soc_name*, defaulting to 125 °C."""
    # Try exact match, then prefix match
    if soc_name in _TJ_MAX_MC:
        return _TJ_MAX_MC[soc_name]
    for key, val in _TJ_MAX_MC.items():
        if key != "_default" and soc_name.startswith(key):
            return val
    return _TJ_MAX_MC["_default"]


# ─────────────────────────────────────────────────────────────────────────────


class THM001CriticalTripOverTjMax(BaseRule):
    """THM-001: Critical trip temperature exceeds Tj_MAX.

    If any trip point's temperature ≥ Tj_MAX the SoC will burn before the OS
    can react — the thermal trip is unreachable in a safe operating window.
    """

    code = "THM-001"
    name = "Critical Trip Temp Exceeds Tj_MAX"
    description = (
        "A thermal trip point temperature is at or above the SoC's absolute "
        "maximum junction temperature (Tj_MAX).  The SoC will burn before "
        "this trip can fire."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        if not model.thermal_zones:
            return violations

        tj_max = _tj_max(context.soc_name)

        for zone_name, zone in model.thermal_zones.items():
            for trip in zone.trips:
                if trip.temperature >= tj_max:
                    violations.append(self._create_violation(
                        message=(
                            f"Thermal zone '{zone_name}': trip '{trip.name}' "
                            f"temperature {trip.temperature // 1000}°C ≥ "
                            f"Tj_MAX {tj_max // 1000}°C for {context.soc_name}."
                        ),
                        impact=(
                            "The SoC will reach permanent damage temperature before "
                            "this trip point fires.  Thermal protection is ineffective."
                        ),
                        suggestion=(
                            f"Set temperature ≤ {(tj_max - 5_000) // 1000}°C "
                            f"({tj_max - 5_000} in DTS millicelsius units)."
                        ),
                        location=f"/thermal-zones/{zone_name}/trips/{trip.name}",
                        affected_nodes=[zone_name],
                    ))
        return violations


class THM002MissingCriticalTrip(BaseRule):
    """THM-002: Thermal zone lacks a critical trip point.

    A thermal zone without a *critical* trip has no last-resort hardware
    shutdown.  Under runaway conditions the OS may freeze before issuing an
    orderly shutdown, causing permanent silicon damage.
    """

    code = "THM-002"
    name = "Thermal Zone Missing Critical Trip"
    description = (
        "A thermal zone defines trips but none of them is of type 'critical'. "
        "Without a critical trip, there is no hardware-enforced last-resort "
        "shutdown path."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        for zone_name, zone in model.thermal_zones.items():
            if not zone.trips:
                continue  # empty zone — THM-003 may catch it
            has_critical = any(t.trip_type == "critical" for t in zone.trips)
            if not has_critical:
                max_temp = zone.max_trip_temp
                violations.append(self._create_violation(
                    message=(
                        f"Thermal zone '{zone_name}' has {len(zone.trips)} trip(s) "
                        f"(max {max_temp // 1000}°C) but no 'critical' trip point."
                    ),
                    impact=(
                        "Without a critical trip, the kernel cannot trigger an "
                        "emergency shutdown.  Sustained overtemperature will "
                        "permanently damage the SoC."
                    ),
                    suggestion=(
                        "Add a critical trip point slightly below Tj_MAX, e.g.:\n"
                        "  cpu_crit: cpu_crit {\n"
                        f"      temperature = <{_tj_max(context.soc_name) - 10_000}>;\n"
                        "      hysteresis = <0>;\n"
                        "      type = \"critical\";\n"
                        "  };"
                    ),
                    location=f"/thermal-zones/{zone_name}",
                    affected_nodes=[zone_name],
                ))
        return violations


class THM003PassiveZeroPollingDelay(BaseRule):
    """THM-003: Passive cooling zone has polling-delay-passive = 0.

    A passive cooling zone with polling-delay-passive = 0 disables the
    periodic thermal monitoring loop.  CPU frequency throttling will never
    activate, causing sustained overtemperature without any throttle response.
    """

    code = "THM-003"
    name = "Passive Zone Zero Polling Delay"
    description = (
        "A thermal zone that has passive trip points also has "
        "polling-delay-passive = 0.  This disables the DTPM throttling loop, "
        "making the passive trip permanently ineffective."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        for zone_name, zone in model.thermal_zones.items():
            has_passive = any(t.trip_type == "passive" for t in zone.trips)
            if has_passive and zone.polling_delay_passive == 0:
                violations.append(self._create_violation(
                    message=(
                        f"Thermal zone '{zone_name}' has passive trip(s) but "
                        f"polling-delay-passive = 0.  Throttling loop is disabled."
                    ),
                    impact=(
                        "CPU/GPU frequency will never be throttled in response to "
                        "temperature.  The board will hit critical trip or Tj_MAX "
                        "without any preceding throttle step."
                    ),
                    suggestion=(
                        "Set polling-delay-passive to a non-zero value, e.g. "
                        "250 ms:\n"
                        "  polling-delay-passive = <250>;"
                    ),
                    location=f"/thermal-zones/{zone_name}",
                    affected_nodes=[zone_name],
                ))
        return violations


# ── Registration ──────────────────────────────────────────────────────────────


def register_thermal_rules(registry, soc_name: str = "common") -> None:
    """Register all THM rules into *registry*."""
    registry.register(THM001CriticalTripOverTjMax(), soc_name)
    registry.register(THM002MissingCriticalTrip(), soc_name)
    registry.register(THM003PassiveZeroPollingDelay(), soc_name)


__all__ = [
    "THM001CriticalTripOverTjMax",
    "THM002MissingCriticalTrip",
    "THM003PassiveZeroPollingDelay",
    "register_thermal_rules",
    "_TJ_MAX_MC",
    "_tj_max",
]
