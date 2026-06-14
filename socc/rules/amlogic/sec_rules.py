"""Amlogic security rules (ML-3xx series).

Amlogic Meson SoCs do not expose a conventional IOMMU; their security model is
built around the ARM **Secure Monitor** (``amlogic,meson-*-sm``).  Several
peripherals are not memory-mapped at all from the Normal world — the kernel
reaches them only through SMC calls handled by the Secure Monitor:

  * the **eFuse** (``amlogic,meson-*-efuse``) — OTP / chip-ID / MAC storage,
  * the secure power and clock-measurement services on some generations.

ML-301  Secure-Monitor-backed peripheral without a Secure Monitor node
    If an eFuse (or other SMC-backed peripheral) is present but no
    ``amlogic,meson-*-sm`` node exists, the driver has no firmware interface to
    call: it fails to probe, and any feature that depends on OTP data — MAC
    address, thermal calibration, secure-boot key state — is silently lost.
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


def _compat_of(node) -> str:
    val = node.properties.get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


class ML301SecureMonitorMissing(BaseRule):
    """ML-301: SMC-backed peripheral present but no Secure Monitor node."""

    code = "ML-301"
    name = "Secure Monitor Missing"
    description = (
        "Amlogic eFuse and related peripherals are reached through SMC calls "
        "handled by the Secure Monitor (amlogic,meson-*-sm).  Without that node "
        "the driver has no firmware interface and fails to probe."
    )
    severity = "error"

    # Compatible substrings for peripherals that require the Secure Monitor.
    _SM_DEPENDENT_TOKENS = ("-efuse", "meson-efuse")
    # Compatible substrings identifying the Secure Monitor itself.
    _SM_TOKENS = ("-sm", "secure-monitor", "meson-gxbb-sm")

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        if not model.devices:
            return violations

        def _has_sm() -> bool:
            for name, node in model.devices.items():
                compat = _compat_of(node)
                if any(tok in compat for tok in self._SM_TOKENS):
                    return True
                if "secure-monitor" in name.lower() or name.lower() == "sm":
                    return True
            return False

        sm_present = _has_sm()
        if sm_present:
            return violations  # firmware interface available — nothing to flag

        for dev_name, dev_node in model.devices.items():
            compat = _compat_of(dev_node)
            if not any(tok in compat for tok in self._SM_DEPENDENT_TOKENS):
                continue
            violations.append(self._create_violation(
                message=(
                    f"Device '{dev_name}' ({compat.split()[0]!r}) needs the "
                    f"Amlogic Secure Monitor, but no amlogic,meson-*-sm node "
                    f"was found."
                ),
                impact=(
                    "eFuse reads go through SMC calls into the Secure Monitor; "
                    "without it the driver cannot probe and OTP-derived data "
                    "(MAC address, thermal calibration, secure-boot state) is "
                    "unavailable."
                ),
                suggestion=(
                    "Add the Secure Monitor firmware node, e.g.:\n"
                    "  firmware {\n"
                    "      sm: secure-monitor {\n"
                    "          compatible = \"amlogic,meson-gxbb-sm\";\n"
                    "      };\n"
                    "  };"
                ),
                location=f"/{dev_name}",
                affected_nodes=[dev_name],
            ))

        return violations


def register_amlogic_sec_rules(registry, soc_name: str) -> None:
    """Register Amlogic security rules."""
    registry.register(ML301SecureMonitorMissing(), soc_name)
