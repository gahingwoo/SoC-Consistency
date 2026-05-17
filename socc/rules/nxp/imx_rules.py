"""NXP i.MX series rules.

IMX-001  ARM PLL Frequency Limit
    The ARM PLL VCO output must not exceed 1800 MHz on i.MX 8M Plus.
    Programming the PLL for a higher output corrupts the clock tree and
    causes an immediate system hang.

IMX-002  DRAM Power Before SoC Core
    NXP Application Note AN13486 mandates that NVCC_DRAM / VDD_DRAM must
    reach its target voltage *before* VDD_SOC is enabled.  Violating this
    order causes DRAM PHY calibration to fail and the SoC will not boot.
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext

# ─────────────────────────────────────────────────────────────────────────────
# IMX-001  ARM PLL Frequency Limit
# ─────────────────────────────────────────────────────────────────────────────
_IMX8MP_ARM_PLL_MAX_MHZ = 1800

# Clock names that carry the ARM PLL output (the A53 domain)
_ARM_PLL_CLOCK_NAMES = {
    "arm_pll",
    "arm_a53_clk",
    "arm_a53",
    "a53_clk",
    "cpu_clk",
    "clk-arm",
    "arm-clk",
}


class IMX001ArmPllFreqLimit(BaseRule):
    """IMX-001: ARM PLL frequency must not exceed 1800 MHz on i.MX 8M Plus."""

    code = "IMX-001"
    name = "ARM PLL Frequency Limit (i.MX8M Plus)"
    description = (
        "The ARM PLL VCO output on i.MX 8M Plus is rated at a maximum of "
        f"{_IMX8MP_ARM_PLL_MAX_MHZ} MHz.  Exceeding this value causes clock "
        "tree instability and system hang."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Walk all clock nodes and inspect those matching ARM PLL names
        for clk_name, clk_node in model.clock_tree.clocks.items():
            name_lower = clk_name.lower()
            if not any(arm in name_lower for arm in _ARM_PLL_CLOCK_NAMES):
                continue

            # Clock.rate is stored in Hz
            rate_hz = getattr(clk_node, "rate", None)
            if rate_hz is None:
                continue
            freq_mhz = rate_hz / 1_000_000

            if freq_mhz is not None and freq_mhz > _IMX8MP_ARM_PLL_MAX_MHZ:
                violations.append(
                    self._create_violation(
                        message=(
                            f"ARM PLL clock '{clk_name}' configured at "
                            f"{freq_mhz:.0f} MHz exceeds the i.MX 8M Plus "
                            f"maximum of {_IMX8MP_ARM_PLL_MAX_MHZ} MHz."
                        ),
                        impact=(
                            "Over-clocking the ARM PLL VCO causes clock tree "
                            "instability; the SoC will hang or reset immediately "
                            "after PLL lock is attempted."
                        ),
                        suggestion=(
                            f"Set the ARM PLL output ≤ {_IMX8MP_ARM_PLL_MAX_MHZ} MHz.  "
                            "Use a PLL divider value that keeps the VCO within spec.  "
                            "Refer to IMX8MPRM §5.1.5 'ARM_PLL' for allowed divider "
                            "combinations."
                        ),
                        location=f"/clocks/{clk_name}",
                        affected_nodes=[clk_name],
                        severity="error",
                    )
                )

        return violations


# ─────────────────────────────────────────────────────────────────────────────
# IMX-002  DRAM Power Before SoC Core
# ─────────────────────────────────────────────────────────────────────────────

# Canonical supply names for DRAM domain on i.MX 8M family
_DRAM_SUPPLY_NAMES = {
    "nvcc_dram",
    "vdd_dram",
    "buck3",
    "dram_vdd",
    "lpddr_vdd",
}

# Canonical supply names for SoC core domain
_SOC_CORE_SUPPLY_NAMES = {
    "vdd_soc",
    "buck1",
    "vdd_core",
    "soc_vdd",
    "vddcore",
}


class IMX002DramBeforeSocCore(BaseRule):
    """IMX-002: NVCC_DRAM must be powered up before VDD_SOC (i.MX 8M requirement)."""

    code = "IMX-002"
    name = "DRAM Power Sequencing (i.MX8M Plus)"
    description = (
        "NXP AN13486 mandates that the DRAM supply (NVCC_DRAM / BUCK3) must "
        "reach its target voltage *before* the SoC core supply (VDD_SOC / BUCK1) "
        "is enabled.  Inverting this sequence causes DRAM PHY calibration failure "
        "and a non-bootable board."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        nodes = model.power_tree.nodes

        # Locate DRAM and SoC-core regulators in the model
        dram_regs = {
            name: reg for name, reg in nodes.items()
            if name.lower() in _DRAM_SUPPLY_NAMES
        }
        core_regs = {
            name: reg for name, reg in nodes.items()
            if name.lower() in _SOC_CORE_SUPPLY_NAMES
        }

        if not dram_regs or not core_regs:
            # Cannot validate — supply names not present in parsed model
            return violations

        for dram_name, dram_reg in dram_regs.items():
            for core_name, core_reg in core_regs.items():
                # sequence_order: lower = earlier.  DRAM must have lower order.
                if dram_reg.sequence_order >= core_reg.sequence_order:
                    violations.append(
                        self._create_violation(
                            message=(
                                f"DRAM supply '{dram_name}' (sequence order "
                                f"{dram_reg.sequence_order}) is not guaranteed to "
                                f"power up before SoC core '{core_name}' (order "
                                f"{core_reg.sequence_order}).  NXP AN13486 requires "
                                "NVCC_DRAM stable BEFORE VDD_SOC."
                            ),
                            impact=(
                                "DRAM PHY calibration runs before DRAM is powered, "
                                "causing calibration failure.  The SoC will not proceed "
                                "past internal ROM / DDR init; board is non-bootable."
                            ),
                            suggestion=(
                                "In the DTS regulator node for NVCC_DRAM add "
                                "'regulator-boot-on' and set 'startup-delay-us' to a "
                                "value that guarantees DRAM is stable before the "
                                "VDD_SOC PMIC output ramps.  "
                                "For PCA9450C: route BUCK3 enable before BUCK1 in "
                                "the PMIC OTP sequencing register."
                            ),
                            location=f"/{dram_name}",
                            affected_nodes=[dram_name, core_name],
                            severity="error",
                        )
                    )

        return violations


# ─────────────────────────────────────────────────────────────────────────────
# Registration helper
# ─────────────────────────────────────────────────────────────────────────────

def register_nxp_rules(registry, soc_name: str = "imx8mp") -> None:
    """Register all NXP i.MX rule instances for *soc_name*."""
    registry.register(IMX001ArmPllFreqLimit(), soc_name)
    registry.register(IMX002DramBeforeSocCore(), soc_name)


__all__ = [
    "IMX001ArmPllFreqLimit",
    "IMX002DramBeforeSocCore",
    "register_nxp_rules",
]
