"""Common power-supply rules (PD-007 / PD-008).

PD-007  IO-before-Core Sequencing
    An IO supply (1.8–3.3 V) that feeds a core supply (<1.2 V) must be
    enabled *before* the core supply in the power tree.  The DTS
    regulator-enable-ramp-delay gives a minimum delay budget; a missing
    parent dependency is flagged.

PD-008  PMIC Channel Current Over-Commitment
    When a PMIC YAML constraint file lists ``max_current_ma`` for a channel
    and the SoC model can estimate total load, a warning is raised when the
    total theoretical draw exceeds the channel limit.
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext

# Voltage thresholds (V) used to classify supply domains
_IO_VOLTAGE_MIN = 1.5
_IO_VOLTAGE_MAX = 3.6
_CORE_VOLTAGE_MAX = 1.25  # anything ≤ this is considered a core supply


def _is_io_supply(voltage_min: float, voltage_max: float) -> bool:
    v_mid = (voltage_min + voltage_max) / 2
    return _IO_VOLTAGE_MIN <= v_mid <= _IO_VOLTAGE_MAX


def _is_core_supply(voltage_min: float, voltage_max: float) -> bool:
    v_mid = (voltage_min + voltage_max) / 2
    return v_mid <= _CORE_VOLTAGE_MAX


class PD007IOBeforeCoreSequence(BaseRule):
    """PD-007: IO supply must precede dependent core supply in the power tree."""

    code = "PD-007"
    name = "IO Power Before Core Power"
    description = (
        "An IO-level supply (1.5–3.6 V) that is the direct parent of a core "
        "supply (≤1.25 V) must appear earlier in the startup sequence.  "
        "A missing parent link means the sequencing constraint cannot be "
        "verified and is treated as a violation."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        nodes = model.power_tree.nodes
        edges = model.power_tree.edges  # parent -> [children]

        for reg_name, reg in nodes.items():
            if not _is_core_supply(reg.voltage_min, reg.voltage_max):
                continue  # only check core supplies

            parents = model.power_tree.reverse_edges.get(reg_name, [])
            if not parents:
                # Core supply with no declared parent — cannot verify sequencing
                violations.append(
                    self._create_violation(
                        message=(
                            f"Core supply {reg_name!r} ({reg.voltage_min:.2f}–"
                            f"{reg.voltage_max:.2f} V) has no declared parent in the "
                            f"power tree — IO-before-core sequencing cannot be verified."
                        ),
                        impact="Potential core-first power-on; may corrupt IO logic state.",
                        suggestion=(
                            f"Add a vin-supply phandle to {reg_name!r} referencing the "
                            f"upstream IO rail (typically 1.8 V or 3.3 V)."
                        ),
                        location=f"/regulators/{reg_name}",
                        affected_nodes=[reg_name],
                    )
                )
                continue

            for parent_name in parents:
                parent = nodes.get(parent_name)
                if parent is None:
                    continue
                # IO parent → core child: check that IO comes first (sequence_order)
                if _is_io_supply(parent.voltage_min, parent.voltage_max):
                    if parent.sequence_order >= reg.sequence_order:
                        violations.append(
                            self._create_violation(
                                message=(
                                    f"IO supply {parent_name!r} (seq {parent.sequence_order}) "
                                    f"does not precede core supply {reg_name!r} "
                                    f"(seq {reg.sequence_order}) — IO-before-core violated."
                                ),
                                impact="Core logic may power up before IO reference is stable.",
                                suggestion=(
                                    f"Increase the sequence_order of {reg_name!r} or add "
                                    f"regulator-enable-ramp-delay to {parent_name!r}."
                                ),
                                location=f"/regulators/{parent_name}",
                                affected_nodes=[parent_name, reg_name],
                            )
                        )

        return violations


class PD008CurrentOvercommit(BaseRule):
    """PD-008: Sum of device loads exceeds PMIC channel current limit."""

    code = "PD-008"
    name = "PMIC Channel Current Over-Commitment"
    description = (
        "When a PMIC YAML spec defines max_current_ma for a regulator channel, "
        "and the total theoretical load (derived from consumer count × average "
        "draw estimate, or explicit metadata) exceeds that limit, a warning is "
        "raised to prompt manual review."
    )
    severity = "warning"

    # Rough per-consumer current estimates by supply type (mA)
    _CONSUMER_DRAW_MA = {
        "core": 500,
        "io": 100,
        "ldo": 50,
        "fixed": 0,     # board-level fixed rail — not our concern
        "unknown": 80,
    }

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Per-regulator current override from metadata (e.g. loaded from YAML constraints)
        current_overrides: dict = context.metadata.get("regulator_current_ma", {})

        for reg_name, reg in model.power_tree.nodes.items():
            limit = reg.max_current_ma
            if limit <= 0:
                # Check if constraint YAML provided a value
                limit = current_overrides.get(reg_name, 0)
            if limit <= 0:
                continue  # no limit known — skip

            # Estimate total draw
            explicit_draws: dict = context.metadata.get("consumer_current_ma", {})
            if explicit_draws:
                total = sum(explicit_draws.get(c, 0) for c in reg.consumers)
            else:
                # Fall back to heuristic based on regulator type and consumer count
                reg_type_key = "core" if _is_core_supply(reg.voltage_min, reg.voltage_max) \
                    else ("io" if _is_io_supply(reg.voltage_min, reg.voltage_max) else "ldo")
                per_consumer = self._CONSUMER_DRAW_MA.get(
                    reg.type.lower(), self._CONSUMER_DRAW_MA[reg_type_key]
                )
                total = len(reg.consumers) * per_consumer

            if total > limit:
                violations.append(
                    self._create_violation(
                        message=(
                            f"[WARNING] PD-008 {reg_name} total theoretical load "
                            f"({total}mA) exceeds PMIC channel limit ({limit}mA)"
                        ),
                        impact=(
                            "PMIC may enter over-current protection (OCP), causing "
                            "unexpected resets or power rail collapse under load."
                        ),
                        suggestion=(
                            f"Reduce consumers on {reg_name!r}, split load across "
                            f"additional channels, or select a higher-current PMIC variant."
                        ),
                        location=f"/regulators/{reg_name}",
                        affected_nodes=[reg_name] + list(reg.consumers),
                    )
                )

        return violations


def register_common_power_rules(registry, soc_name: str = "common") -> None:
    """Register PD-007 and PD-008 into *registry*."""
    registry.register(PD007IOBeforeCoreSequence(), soc_name)
    registry.register(PD008CurrentOvercommit(), soc_name)


__all__ = [
    "register_common_power_rules",
    "PD007IOBeforeCoreSequence",
    "PD008CurrentOvercommit",
]
