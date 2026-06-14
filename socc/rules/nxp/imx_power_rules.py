"""NXP i.MX power rules (IMX-2xx series).

The i.MX 8M / 9 families pair the SoC with a companion PMIC (PCA9450 from
NXP, or BD718x7 from ROHM) that supplies the VDD_ARM and VDD_SOC core rails.

IMX-201  Companion PMIC missing
    Without the PMIC node the kernel cannot adjust core voltages for DVFS;
    the board runs at the bootloader's fixed voltage or fails to boot.

IMX-202  Core rail (VDD_ARM / VDD_SOC) missing
    Both rails must be present and supplied by the PMIC.  A missing rail means
    a CPU cluster or the SoC fabric has no controllable supply.
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


def _compat_of(node) -> str:
    val = getattr(node, "properties", {}).get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


class IMX201PmicMissing(BaseRule):
    """IMX-201: Companion PMIC node missing."""

    code = "IMX-201"
    name = "Companion PMIC Missing (i.MX)"
    description = (
        "i.MX 8M / 9 boards rely on a PCA9450 or BD718x7 PMIC for DVFS-capable "
        "core rails.  Without it the kernel cannot scale VDD_ARM / VDD_SOC."
    )
    severity = "warning"

    _PMIC_TOKENS = (
        "nxp,pca9450a", "nxp,pca9450b", "nxp,pca9450c",
        "rohm,bd71837", "rohm,bd71847", "rohm,bd71850",
        "fsl,pf8x00", "nxp,pf8100", "nxp,pf8121a", "nxp,pf5300",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        found = any(
            any(tok in _compat_of(node) for tok in self._PMIC_TOKENS)
            for node in model.devices.values()
        )
        if not found:
            found = any(
                "pmic" in n.lower() or "pca9450" in n.lower() or "bd718" in n.lower()
                for n in model.power_tree.nodes
            )

        if not found:
            violations.append(
                self._create_violation(
                    message=(
                        "No companion PMIC (PCA9450 / BD718x7) found.  "
                        "DVFS core-rail control is unavailable."
                    ),
                    impact=(
                        "The CPU runs at a single fixed voltage; cpufreq cannot "
                        "raise VDD_ARM for high OPPs, so the top frequencies are "
                        "unreachable or unstable."
                    ),
                    suggestion=(
                        "Add the PMIC under the I2C controller it sits on, e.g.:\n"
                        "  pmic@25 {\n"
                        "      compatible = \"nxp,pca9450c\";\n"
                        "      reg = <0x25>;\n"
                        "      regulators { ... };\n"
                        "  };"
                    ),
                    location="/soc",
                    affected_nodes=["pmic"],
                )
            )

        return violations


class IMX202CoreRailMissing(BaseRule):
    """IMX-202: VDD_ARM / VDD_SOC core rail missing from the power tree."""

    code = "IMX-202"
    name = "Core Power Rail Missing (i.MX)"
    description = (
        "VDD_ARM supplies the CPU cluster and VDD_SOC supplies the SoC fabric. "
        "Both must be present as regulators in the power tree."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        names = [n.lower() for n in model.power_tree.nodes]

        # Don't fire on a model with no power tree at all (parser found nothing);
        # the PMIC-missing rule already covers that case.
        if not names:
            return violations

        rail_aliases = {
            "vdd_arm": ("vdd_arm", "vddarm", "buck2", "arm_dvfs"),
            "vdd_soc": ("vdd_soc", "vddsoc", "buck1", "soc_dvfs"),
        }
        for rail, aliases in rail_aliases.items():
            if not any(any(a in n for a in aliases) for n in names):
                violations.append(
                    self._create_violation(
                        message=(
                            f"Required i.MX core rail '{rail}' not found in the "
                            f"power tree (looked for {', '.join(aliases)})."
                        ),
                        impact=(
                            f"The domain supplied by {rail} has no controllable "
                            "regulator; DVFS and suspend voltage management break."
                        ),
                        suggestion=(
                            f"Expose {rail} as a PMIC buck regulator and reference "
                            f"it from the CPU OPP table via 'cpu-supply' / "
                            f"'{rail.replace('vdd_', '')}-supply'."
                        ),
                        location="/power",
                        affected_nodes=[rail],
                    )
                )

        return violations


def register_nxp_power_rules(registry, soc_name: str) -> None:
    """Register NXP i.MX power rules."""
    registry.register(IMX201PmicMissing(), soc_name)
    registry.register(IMX202CoreRailMissing(), soc_name)
