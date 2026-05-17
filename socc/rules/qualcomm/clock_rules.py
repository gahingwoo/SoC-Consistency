"""Qualcomm clock domain rules.

Qualcomm platforms use a Global Clock Controller (GCC) as the primary
clock provider, plus optional sub-system clock controllers (CAMcc, DISPcc,
VIDEOcc, GPUcc).  The GCC node is mandatory.

Key pitfalls:
- GCC node missing entirely (most other devices fail to get clocks)
- Wrong compatible string (mismatched SoC variant)
- DSP/subsystem clocks referenced without the corresponding clock controller
"""

from typing import List

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


class QC101GCCMissing(BaseRule):
    """QC-101: Global Clock Controller (GCC) node missing."""

    code = "QC-101"
    name = "GCC Clock Controller Missing"
    description = (
        "The Global Clock Controller (GCC) is the primary clock provider on "
        "Qualcomm SoCs.  Its absence means most peripheral clocks are unavailable."
    )
    severity = "error"

    _GCC_COMPATIBLES = (
        "qcom,sdm845-gcc",
        "qcom,sm8250-gcc",
        "qcom,sc7180-gcc",
        "qcom,qcs6490-gcc",
        "qcom,sm8150-gcc",
        "qcom,sm8350-gcc",
        "qcom,sm8450-gcc",
        "qcom,sc8280xp-gcc",
        "qcom,msm8996-gcc",
        "qcom,msm8998-gcc",
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        found = any(
            any(compat in str(dev) for compat in self._GCC_COMPATIBLES)
            for dev in model.devices
        )

        if not found:
            gcc_in_tree = any(
                "gcc" in n.lower() for n in model.clock_tree.nodes
            )
            if not gcc_in_tree:
                violations.append(
                    self._create_violation(
                        message=(
                            "No Qualcomm GCC (Global Clock Controller) node found. "
                            "GCC is the root clock provider for most SoC peripherals."
                        ),
                        impact=(
                            "USB, PCIe, SPI, I2C, UART, and most other peripherals "
                            "will fail to obtain clocks and will not probe."
                        ),
                        suggestion=(
                            "Add a GCC clock controller node, e.g.:\n"
                            "  gcc: clock-controller@100000 {\n"
                            "    compatible = \"qcom,sdm845-gcc\";\n"
                            "    reg = <0 0x00100000 0 0x1f0000>;\n"
                            "    #clock-cells = <1>;\n"
                            "    #reset-cells = <1>;\n"
                            "    #power-domain-cells = <1>;\n"
                            "  };"
                        ),
                        location="/soc",
                        affected_nodes=["gcc"],
                    )
                )

        return violations


class QC102InvalidClockFrequency(BaseRule):
    """QC-102: Clock frequency outside Qualcomm OPP table bounds."""

    code = "QC-102"
    name = "Clock Frequency Outside OPP Bounds"
    description = (
        "CPU or GPU clock frequencies must not exceed the maximum operating point "
        "defined in the OPP table for the SoC."
    )
    severity = "warning"

    # Conservative sanity limits per SoC class (Hz)
    _MAX_CPU_HZ = 3_200_000_000   # 3.2 GHz — highest Snapdragon prime core
    _MAX_GPU_HZ = 1_000_000_000   # 1.0 GHz — headroom for Adreno 7xx
    _REF_XO_HZ = 19_200_000       # Qualcomm XO

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for node_name, clock_node in model.clock_tree.nodes.items():
            freq = getattr(clock_node, "frequency_hz", None)
            if freq is None:
                continue

            if "cpu" in node_name.lower() and freq > self._MAX_CPU_HZ:
                violations.append(
                    self._create_violation(
                        message=(
                            f"CPU clock {node_name!r} set to {freq / 1e6:.0f} MHz, "
                            f"which exceeds the Qualcomm max of {self._MAX_CPU_HZ / 1e6:.0f} MHz."
                        ),
                        impact="Over-clocking causes thermal throttle, instability, or chip damage.",
                        suggestion="Set frequency to the SoC's peak OPP; consult the ACPM/LMFS table.",
                        location=f"/cpus/{node_name}",
                        affected_nodes=[node_name],
                    )
                )
            elif "gpu" in node_name.lower() and freq > self._MAX_GPU_HZ:
                violations.append(
                    self._create_violation(
                        message=(
                            f"GPU clock {node_name!r} set to {freq / 1e6:.0f} MHz, "
                            f"exceeds Adreno maximum."
                        ),
                        impact="GPU over-frequency causes lockups under 3D load.",
                        suggestion="Use the peak OPP from the gpucc OPP table.",
                        location=f"/gpu/{node_name}",
                        affected_nodes=[node_name],
                    )
                )

        return violations


class QC103SubsystemFirmwareMissing(BaseRule):
    """QC-103: DSP/subsystem firmware load path not declared."""

    code = "QC-103"
    name = "Subsystem Firmware Node Missing"
    description = (
        "Qualcomm DSP subsystems (ADSP, CDSP, MODEM) require a remoteproc node "
        "with a firmware-name property.  Without it, the subsystem will not start "
        "and dependent services (audio, AI, modem) are unavailable."
    )
    severity = "warning"

    _SUBSYSTEM_NODES = ("adsp", "cdsp", "mpss", "slpi")

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Check if any remoteproc/subsystem node is present
        has_remoteproc = any(
            "remoteproc" in dev.lower() or "adsp" in dev.lower() or "cdsp" in dev.lower()
            for dev in model.devices
        )

        # Only warn if there are clock consumers but no remoteproc
        has_subsys_clocks = any(
            any(sub in clk.lower() for sub in self._SUBSYSTEM_NODES)
            for clks in model.device_clocks.values()
            for clk in clks
        )

        if has_subsys_clocks and not has_remoteproc:
            violations.append(
                self._create_violation(
                    message=(
                        "Subsystem clocks are referenced but no remoteproc/subsystem node found. "
                        "ADSP/CDSP firmware will not be loaded."
                    ),
                    impact="Audio routing via ADSP and AI inference via CDSP will be unavailable.",
                    suggestion=(
                        "Add remoteproc nodes for each required subsystem:\n"
                        "  remoteproc@17300000 {\n"
                        "    compatible = \"qcom,sm8250-adsp-pas\";\n"
                        "    firmware-name = \"qcom/sm8250/adsp.mbn\";\n"
                        "  };"
                    ),
                    location="/soc",
                    affected_nodes=list(self._SUBSYSTEM_NODES),
                )
            )

        return violations


def register_qualcomm_clock_rules(registry, soc_name: str) -> None:
    """Register Qualcomm clock rules."""
    registry.register(QC101GCCMissing(), soc_name)
    registry.register(QC102InvalidClockFrequency(), soc_name)
    registry.register(QC103SubsystemFirmwareMissing(), soc_name)
