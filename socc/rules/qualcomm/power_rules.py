"""Qualcomm power domain rules.

Qualcomm SoCs use RPMh (Resource Power Manager-hardened) to manage
power domains.  Devices must reference either rpmpd or rpmhpd depending
on the SoC generation.  The critical rails are cx, mx, and mmcx.

Common mistakes:
- Missing rpmpd/rpmhpd node (device won't boot)
- Device references wrong rail (cx vs mmcx for multimedia peripherals)
- SPMI PMIC bus missing (no battery charging, no rail control)
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


class QC001RPMhMissing(BaseRule):
    """QC-001: RPMh power domain provider node missing."""

    code = "QC-001"
    name = "RPMh Power Domain Provider Missing"
    description = (
        "Qualcomm SoCs require an RPMh (or RPM) power domain provider node. "
        "Without it, all power-managed devices fail to probe and suspend/resume breaks."
    )
    severity = "error"

    _RPMH_COMPATIBLES = (
        "qcom,rpmhpd",
        "qcom,rpmpd",
        "qcom,sm8250-rpmhpd",
        "qcom,sdm845-rpmpd",
        "qcom,sc7180-rpmhpd",
        "qcom,qcs6490-rpmhpd",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        found = any(
            any(compat in str(dev) for compat in self._RPMH_COMPATIBLES)
            for dev in model.devices
        )

        if not found:
            # Also check power tree nodes for any rpmh-compatible name
            rpmh_in_tree = any(
                "rpmh" in n.lower() or "rpmpd" in n.lower()
                for n in model.power_tree.nodes
            )
            if not rpmh_in_tree:
                violations.append(
                    self._create_violation(
                        message=(
                            "No RPMh/RPMPD power domain provider found in the device tree. "
                            "Qualcomm platforms require qcom,rpmhpd or qcom,rpmpd."
                        ),
                        impact=(
                            "All power-domain consumers (GPU, USB, modem, camera) will "
                            "fail to probe.  System may not suspend/resume correctly."
                        ),
                        suggestion=(
                            "Add an rpmhpd node:\n"
                            "  rpmhpd: power-controller {\n"
                            "    compatible = \"qcom,rpmhpd\";\n"
                            "    #power-domain-cells = <1>;\n"
                            "    operating-points-v2 = <&rpmhpd_opp_table>;\n"
                            "  };"
                        ),
                        location="/soc",
                        affected_nodes=["rpmhpd"],
                    )
                )

        return violations


class QC002SPMIMissing(BaseRule):
    """QC-002: SPMI bus / PMIC node missing."""

    code = "QC-002"
    name = "SPMI PMIC Bus Missing"
    description = (
        "Qualcomm platforms use a SPMI bus to communicate with the companion PMIC "
        "(PM8998/PM8150/PM6150).  Without it, voltage rail control, charging, and "
        "battery management are non-functional."
    )
    severity = "warning"

    _SPMI_COMPATIBLES = (
        "qcom,spmi-pmic-arb",
        "qcom,pmic-arb",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        found = any(
            any(compat in str(dev) for compat in self._SPMI_COMPATIBLES)
            for dev in model.devices
        )

        # also accept if power tree has pmic-labelled nodes
        if not found:
            pmic_nodes = [
                n for n in model.power_tree.nodes
                if "pmic" in n.lower() or "spmi" in n.lower()
            ]
            if not pmic_nodes:
                violations.append(
                    self._create_violation(
                        message=(
                            "No SPMI PMIC bus (qcom,spmi-pmic-arb) found. "
                            "Companion PMIC is absent from the device tree."
                        ),
                        impact=(
                            "Battery charging, USB-PD, regulator control, and "
                            "key voltage rails will not function."
                        ),
                        suggestion=(
                            "Add the SPMI controller node and corresponding PMIC child:\n"
                            "  spmi@c440000 { compatible = \"qcom,spmi-pmic-arb\"; ... };"
                        ),
                        location="/soc",
                        affected_nodes=["spmi"],
                    )
                )

        return violations


class QC003CXRailMissing(BaseRule):
    """QC-003: CX (core logic) power rail not present in power tree."""

    code = "QC-003"
    name = "CX Power Rail Missing"
    description = (
        "The CX rail is the primary logic supply on Qualcomm SoCs.  "
        "It must be present as a power-domain or regulator node."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        has_cx = any(
            "cx" in n.lower() or "vdd_cx" in n.lower()
            for n in model.power_tree.nodes
        )
        has_mx = any(
            "mx" in n.lower() or "vdd_mx" in n.lower()
            for n in model.power_tree.nodes
        )

        for rail, present in [("cx", has_cx), ("mx", has_mx)]:
            if not present:
                violations.append(
                    self._create_violation(
                        message=f"Required Qualcomm power rail '{rail}' not defined in power tree.",
                        impact=(
                            f"All devices in the '{rail}' power domain will fail to probe; "
                            "deep sleep is broken without CX/MX vote management."
                        ),
                        suggestion=(
                            f"Ensure the rpmhpd node exposes the '{rail}' power domain, "
                            f"or add a regulator named vdd_{rail} that maps to the PMIC rail."
                        ),
                        location="/power-controller",
                        affected_nodes=[rail],
                    )
                )

        return violations


def register_qualcomm_power_rules(registry, soc_name: str) -> None:
    """Register Qualcomm power domain rules."""
    registry.register(QC001RPMhMissing(), soc_name)
    registry.register(QC002SPMIMissing(), soc_name)
    registry.register(QC003CXRailMissing(), soc_name)
