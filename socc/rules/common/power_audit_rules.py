"""Power-management audit rules (PWR-1xx series).

PWR-101  Always-On regulator on a non-critical supply
PWR-102  PMIC / power management IC missing wakeup-source

These rules audit the *suspend/resume* behaviour of the power tree:

PWR-101 — ``regulator-always-on`` prevents the kernel from turning off a
  regulator during suspend.  This is intentional for core rails (CPU, DDR,
  SoC fabric) but a power-leak bug when applied to peripheral supplies (USB
  hub, SD card, camera, display backlight, WiFi, audio CODEC).

PWR-102 — A PMIC node that is the primary system power controller must be
  reachable from the resume path.  If it lives behind an I2C controller and
  that controller is suspended before the PMIC's wakeup interrupt fires, the
  system will hang on resume.  The ``wakeup-source`` property instructs the
  kernel to keep the I2C controller and IRQ path active through suspend.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Set

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Keyword classifiers ───────────────────────────────────────────────────────

# Keywords in a device name / compatible that indicate it is a "core" resource
# which legitimately needs always-on power (CPU cluster, DDR, SoC fabric, PLL).
_CRITICAL_KEYWORDS: FrozenSet[str] = frozenset({
    "cpu", "ddr", "dram", "lpddr", "sdram",
    "core", "npu", "gpu",
    "soc", "fabric", "interconnect",
    "pll", "cru", "pmucru",
    "arm",
})

# Keywords that identify peripherals expected to be power-gated during suspend.
_PERIPHERAL_KEYWORDS: FrozenSet[str] = frozenset({
    "usb", "typec",
    "sdmmc", "sdio", "mmc", "emmc", "sdcard",
    "camera", "csi", "ov", "ar",  # camera sensor compat prefixes
    "backlight", "bl",
    "wifi", "bt", "bluetooth", "wlan",
    "audio", "codec", "amp", "i2s", "sai", "es8", "nau", "rt56",
    "led", "rgb",
    "eth", "phy",
    "hub",
})

# Compatible-string or node-name keywords that positively identify a PMIC.
_PMIC_KEYWORDS: FrozenSet[str] = frozenset({
    "pmic",
    # Common PMIC model prefixes/names
    "rk808", "rk809", "rk817", "rk818",
    "tps65", "tps6286",
    "axp20", "axp202", "axp209", "axp22", "axp803", "axp813", "axp818",
    "mt6397", "mt6358", "mt6360",
    "bd71847", "bd71850",
    "pf5030", "pf0900",
    "mp8859", "mp5416",
    "slg3l",
    "sy8113", "sy8827",
    "fan5355", "fan53555",
    "da9063", "da9121",
    "max77686", "max77802", "max8998",
    "lp87565", "lp873x",
    "act8945a",
    "rt5759", "rt6190",
    "s2mps", "s2mpb",
})


def _get_compatible_str(props: dict) -> str:
    """Flatten a ``compatible`` property value to a single lowercase string."""
    compat = props.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return " ".join(str(c) for c in compat).lower()
    return str(compat).lower()


def _has_keyword(text: str, keywords: FrozenSet[str]) -> bool:
    return any(kw in text for kw in keywords)


# ── Reverse supply map helper ─────────────────────────────────────────────────


def _build_consumers_map(model: SoC) -> Dict[str, List[str]]:
    """Return a mapping of supply/regulator name → list of consuming device names."""
    supply_to_consumers: Dict[str, List[str]] = {}
    for dev_name, supplies in model.device_supplies.items():
        for supply in supplies:
            # strip phandle syntax ( &foo ) if present
            supply_clean = supply.strip().lstrip("&")
            supply_to_consumers.setdefault(supply_clean, []).append(dev_name)
    # Also look at power_tree consumer lists
    for reg_name, reg in model.power_tree.nodes.items():
        for consumer in reg.consumers:
            consumer_clean = consumer.strip().lstrip("&")
            supply_to_consumers.setdefault(reg_name, [])
            if consumer_clean not in supply_to_consumers[reg_name]:
                supply_to_consumers[reg_name].append(consumer_clean)
    return supply_to_consumers


# ── PWR-101 ───────────────────────────────────────────────────────────────────


class PWR101AlwaysOnPeripheralRail(BaseRule):
    """PWR-101: ``regulator-always-on`` on a non-critical peripheral supply.

    If a regulator powers only peripheral devices (USB, SD, camera, audio,
    WiFi, backlight, Ethernet) and is marked ``regulator-always-on``, the
    supply cannot be disabled during system suspend.  This wastes power and
    can cause significant standby current leakage.

    The rule fires with *warning* severity (not error) because ``regulator-
    always-on`` is occasionally required to work around missing regulator-
    control logic, but it should always be reviewed.
    """

    code = "PWR-101"
    name = "Always-On Regulator on Non-Critical Peripheral Rail"
    description = (
        "A regulator is marked regulator-always-on but appears to supply only "
        "peripheral devices that should be power-gated during suspend.  "
        "This prevents the kernel power management from disabling the rail "
        "during sleep, causing standby power leakage."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        consumers_map = _build_consumers_map(model)

        for node_name, ir_node in model.devices.items():
            props = ir_node.properties

            # Only look at regulator nodes with always-on flag
            if "regulator-always-on" not in props:
                continue

            # Exclude the node itself if it looks like a core/critical resource
            compat = _get_compatible_str(props)
            node_lower = node_name.lower()
            combined = node_lower + " " + compat

            if _has_keyword(combined, _CRITICAL_KEYWORDS):
                continue  # legitimately always-on (CPU rail, DDR rail, etc.)

            # Gather this regulator's known consumers
            consumers = consumers_map.get(node_name, [])
            # Also check by label (power_tree keys may be labelled names)
            label = props.get("label", "")
            if label and label != node_name:
                consumers = consumers + consumers_map.get(str(label), [])

            if not consumers:
                # No consumer data → raise informational finding
                violations.append(
                    self._create_violation(
                        message=(
                            f"{node_name}: regulator-always-on set but no "
                            f"consumers found — verify this is intentional."
                        ),
                        impact=(
                            "The regulator stays on through suspend even "
                            "though no device is known to depend on it.  "
                            "If this is a peripheral rail the flag is likely "
                            "a copy-paste mistake."
                        ),
                        suggestion=(
                            f"Remove regulator-always-on from {node_name} "
                            f"unless this supply feeds a core component "
                            f"required to keep the SoC alive during suspend."
                        ),
                        location=ir_node.path,
                        affected_nodes=[node_name],
                        severity="info",
                    )
                )
                continue

            # Classify consumers
            critical_consumers: List[str] = []
            peripheral_consumers: List[str] = []
            for c in consumers:
                c_lower = c.lower()
                if _has_keyword(c_lower, _CRITICAL_KEYWORDS):
                    critical_consumers.append(c)
                elif _has_keyword(c_lower, _PERIPHERAL_KEYWORDS):
                    peripheral_consumers.append(c)

            if critical_consumers:
                continue   # feeds a core resource — always-on is justified

            if peripheral_consumers:
                consumers_str = ", ".join(peripheral_consumers[:4])
                violations.append(
                    self._create_violation(
                        message=(
                            f"{node_name}: regulator-always-on on peripheral "
                            f"supply (consumers: {consumers_str})."
                        ),
                        impact=(
                            f"Rail {node_name} will remain powered during "
                            f"system suspend, causing standby current leakage "
                            f"on the following peripherals: {consumers_str}."
                        ),
                        suggestion=(
                            f"Remove regulator-always-on from {node_name}. "
                            f"Ensure the peripheral drivers implement proper "
                            f"regulator_enable/disable calls, or that the "
                            f"board design does not require this rail to "
                            f"remain active through suspend."
                        ),
                        location=ir_node.path,
                        affected_nodes=[node_name] + peripheral_consumers[:4],
                    )
                )

        return violations


# ── PWR-102 ───────────────────────────────────────────────────────────────────


class PWR102PMICMissingWakeupSource(BaseRule):
    """PWR-102: PMIC control node is missing ``wakeup-source``.

    A Power Management IC is the root of the system's power tree.  On most
    embedded designs the PMIC is controlled via I2C.  If the I2C bus
    controller is suspended before the PMIC's interrupt line fires during
    resume, the kernel cannot communicate with the PMIC to restore the
    power state — the board hangs indefinitely on resume.

    ``wakeup-source;`` on the PMIC DTS node instructs the kernel to keep the
    I2C bus controller and the IRQ path active through suspend, ensuring the
    PMIC can wake the system.
    """

    code = "PWR-102"
    name = "PMIC Control Node Missing wakeup-source"
    description = (
        "A PMIC device node does not have the wakeup-source property.  "
        "Without it the I2C/SPI bus that connects the PMIC to the SoC may "
        "be fully suspended before the PMIC interrupt fires on resume, "
        "causing a system hang."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, ir_node in model.devices.items():
            props = ir_node.properties

            compat = _get_compatible_str(props)
            node_lower = node_name.lower()
            combined = node_lower + " " + compat

            if not _has_keyword(combined, _PMIC_KEYWORDS):
                continue

            # Only flag if the node is enabled
            status = props.get("status", "okay")
            if status not in ("okay", "ok", ""):
                continue

            # Check for wakeup-source
            if "wakeup-source" in props:
                continue

            violations.append(
                self._create_violation(
                    message=(
                        f"PMIC node {node_name!r} is missing the "
                        f"wakeup-source property."
                    ),
                    impact=(
                        "During deep sleep (suspend-to-RAM) the I2C bus "
                        "controller may be powered off before the PMIC can "
                        "signal a wakeup event.  The system will hang on "
                        "resume with no error output — only PMIC watchdog "
                        "or manual power-cycle can recover it."
                    ),
                    suggestion=(
                        f"Add  wakeup-source;  to the {node_name} DTS node:\n"
                        f"\n"
                        f"    &{node_name} {{\n"
                        f"        wakeup-source;\n"
                        f"    }};\n"
                        f"\n"
                        f"Also ensure the PMIC interrupt pin is mapped in "
                        f"the interrupt-controller and has an appropriate "
                        f"GPIO wake configuration (gpio-key,wakeup)."
                    ),
                    location=ir_node.path,
                    affected_nodes=[node_name],
                )
            )

        return violations


# ── Registration ──────────────────────────────────────────────────────────────


def register_power_audit_rules(registry, soc_name: str = "common") -> None:
    """Register PWR-1xx rules into *registry*."""
    registry.register(PWR101AlwaysOnPeripheralRail(), soc_name)
    registry.register(PWR102PMICMissingWakeupSource(), soc_name)


__all__ = [
    "PWR101AlwaysOnPeripheralRail",
    "PWR102PMICMissingWakeupSource",
    "register_power_audit_rules",
]
