"""Thermal zone and trip-point data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ThermalTrip:
    """A single thermal trip point inside a thermal zone."""

    name: str
    trip_type: str      # "passive" | "hot" | "critical" | "active"
    temperature: int    # millicelsius (as seen in DTS)
    hysteresis: int = 2_000  # millicelsius


@dataclass
class ThermalZone:
    """One `thermal-zones {}` entry from DTS (one cooling domain)."""

    name: str
    polling_delay: int = 1_000          # ms (polling-delay DTS property)
    polling_delay_passive: int = 250    # ms (polling-delay-passive DTS property)
    trips: List[ThermalTrip] = field(default_factory=list)
    cooling_devices: List[str] = field(default_factory=list)
    has_sensor: bool = True             # False when thermal-sensors binding is absent

    @property
    def critical_trip(self) -> ThermalTrip | None:
        """Return the critical trip point if present."""
        for t in self.trips:
            if t.trip_type == "critical":
                return t
        return None

    @property
    def max_trip_temp(self) -> int:
        """Return the highest trip temperature across all trips (millicelsius)."""
        if not self.trips:
            return 0
        return max(t.temperature for t in self.trips)


__all__ = ["ThermalTrip", "ThermalZone"]
