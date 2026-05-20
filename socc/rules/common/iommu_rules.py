"""IOMMU/SMMU binding audit rules (DMA series).

DMA-capable devices that lack ``iommus`` entries bypass the IOMMU entirely.
On Rockchip RK3588, Qualcomm SM8250/SC7180, NXP i.MX8MP and similar SoCs
with an IOMMU/SMMU, this causes:

  * Silent DMA mapping without memory isolation
  * Potential information leakage between subsystems
  * Kernel WARN at driver probe ("no iommu group")
  * IOMMU security boundaries violated (OWASP A01 / memory safety)

Rules
─────
DMA-001  DMA-capable device node lacks required ``iommus`` property
DMA-002  ``iommus`` phandle references an undefined IOMMU controller
"""

from __future__ import annotations

from typing import FrozenSet, List

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Classifier: compatible substrings for DMA-capable devices ────────────────
# Any node whose compatible contains one of these tokens is assumed to be a
# DMA bus master and therefore requires an ``iommus`` binding on SoCs that
# expose an IOMMU.  Extend this set as new device classes are discovered.

_DMA_MASTER_TOKENS: FrozenSet[str] = frozenset({
    # GPU
    "gpu", "mali", "bifrost", "panfrost", "valhall",
    # Video codec
    "vpu", "vdec", "venc", "vepu", "rkvdec", "rkvenc",
    # Image signal processor
    "rkisp", "isp",
    # Display / DRM
    "vop", "display", "drm", "hdmi",
    # NPU / ML accelerator
    "npu", "rknn", "rknn-core",
    # DMA engines
    "dma", "pl330", "axi-dmac",
    # USB host (XHCI uses DMA)
    "xhci", "ehci", "dwc3", "dwc2",
    # PCIe root complex
    "pcie",
    # Ethernet MAC
    "gmac", "emac", "stmmac",
    # Camera/sensor via DMA
    "mipi-csi", "csi2",
    # Audio DMA
    "i2s", "spdif",
})

# Compatible substrings that identify IOMMU / SMMU controllers
_IOMMU_CONTROLLER_TOKENS: FrozenSet[str] = frozenset({
    "iommu", "smmu", "iommu-v1", "iommu-v2",
    "rockchip,iommu", "arm,smmu", "arm,mmu-500",
    "qcom,iommu", "qcom,smmu-500",
    "fsl,imx8mp-iommu", "fsl,imx-iommu",
    "allwinner,sun50i-iommu",
})


def _compat_str(props: dict) -> str:
    val = props.get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


def _is_dma_master(compat: str) -> bool:
    return any(tok in compat for tok in _DMA_MASTER_TOKENS)


def _is_iommu_controller(compat: str) -> bool:
    return any(tok in compat for tok in _IOMMU_CONTROLLER_TOKENS)


# ─────────────────────────────────────────────────────────────────────────────


class DMA001MissingIommuBinding(BaseRule):
    """DMA-001: DMA-capable device lacks ``iommus`` property.

    On SoCs that have an IOMMU (see ``iommu_controllers`` in the constraint
    metadata), every DMA bus master must declare ``iommus = <&iommu DOMAIN_ID>``
    to be assigned to an isolation group.  Devices without this binding bypass
    the IOMMU and can address all of physical memory unconstrained.

    The rule is suppressed when no IOMMU controller exists in the model
    (i.e. the SoC genuinely has no IOMMU) to avoid false positives on
    microcontroller-class SoCs.
    """

    code = "DMA-001"
    name = "DMA-Capable Device Missing IOMMU Binding"
    description = (
        "A DMA-capable device node does not declare ``iommus``.  "
        "On SoCs with an IOMMU/SMMU, this bypasses memory isolation and "
        "allows the device to access all of physical memory unconstrained."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        if not model.devices:
            return violations

        # Detect whether this SoC has any IOMMU controller nodes.
        # If there are none, skip the rule (bare-metal SoCs without IOMMU).
        iommu_controllers: List[str] = []
        for dev_name, dev_node in model.devices.items():
            compat = _compat_str(dev_node.properties)
            if _is_iommu_controller(compat):
                iommu_controllers.append(dev_name)

        # Also accept an explicit list from constraint metadata.
        meta_controllers = context.metadata.get("iommu_controllers", [])
        iommu_controllers = iommu_controllers or meta_controllers

        if not iommu_controllers:
            return violations  # SoC has no IOMMU — rule is N/A

        for dev_name, dev_node in model.devices.items():
            compat = _compat_str(dev_node.properties)
            if not _is_dma_master(compat):
                continue
            if _is_iommu_controller(compat):
                continue  # skip the controller itself
            # display-subsystem is a virtual bus aggregator, not a DMA master itself.
            if "display-subsystem" in compat:
                continue
            has_iommus = dev_node.has_property("iommus")
            if not has_iommus:
                violations.append(self._create_violation(
                    message=(
                        f"Device '{dev_name}' ({compat.split()[0]!r}) is a DMA "
                        f"bus master but has no 'iommus' property."
                    ),
                    impact=(
                        "Without an IOMMU group assignment the device can perform "
                        "DMA to any physical address.  This bypasses memory "
                        "isolation and may allow information leakage between "
                        "subsystems or overwrite kernel memory."
                    ),
                    suggestion=(
                        f"Add an iommus binding to {dev_name!r}, e.g.:\n"
                        f"  &{dev_name} {{\n"
                        f"      iommus = <&{iommu_controllers[0]} 0>;\n"
                        f"  }};\n"
                        "Replace 0 with the correct IOMMU stream/domain ID from "
                        "the SoC TRM."
                    ),
                    location=f"/{dev_name}",
                    affected_nodes=[dev_name],
                ))
        return violations


class DMA002IommuPhandelUndefined(BaseRule):
    """DMA-002: ``iommus`` phandle references an undefined IOMMU controller.

    The label referenced inside ``iommus = <&label ...>`` must resolve to an
    existing IOMMU controller node.  A dangling phandle reference produces a
    dtc compile warning and causes the kernel to silently skip IOMMU setup for
    the device.
    """

    code = "DMA-002"
    name = "IOMMU Phandle Undefined"
    description = (
        "The ``iommus`` property references an IOMMU controller that is not "
        "defined in the device tree.  The kernel will skip IOMMU attachment "
        "for this device, leaving it unconstrained."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        if not model.devices:
            return violations

        # Collect known IOMMU controller labels
        known_iommu_labels: set = set()
        for dev_name, dev_node in model.devices.items():
            compat = _compat_str(dev_node.properties)
            if _is_iommu_controller(compat):
                known_iommu_labels.add(dev_name)
                known_iommu_labels.add(f"&{dev_name}")

        for dev_name, dev_node in model.devices.items():
            iommus_val = dev_node.properties.get("iommus")
            if iommus_val is None:
                continue
            # Phandle refs are stored as strings like "&iommu" or lists thereof
            refs = []
            if isinstance(iommus_val, str):
                refs = [iommus_val]
            elif isinstance(iommus_val, (list, tuple)):
                refs = [str(v) for v in iommus_val if isinstance(v, str) and v.startswith("&")]
            for ref in refs:
                label = ref.lstrip("&")
                if label not in known_iommu_labels and ref not in known_iommu_labels:
                    violations.append(self._create_violation(
                        message=(
                            f"Device '{dev_name}' iommus references '{ref}' "
                            f"which is not a defined IOMMU controller."
                        ),
                        impact=(
                            "Dangling phandle — dtc will warn and the kernel "
                            "will skip IOMMU attachment for this device."
                        ),
                        suggestion=(
                            f"Define the IOMMU controller node with label '{label}', "
                            "or correct the phandle reference in the iommus property."
                        ),
                        location=f"/{dev_name}",
                        affected_nodes=[dev_name, ref],
                    ))
        return violations


# ── Registration ──────────────────────────────────────────────────────────────


def register_iommu_rules(registry, soc_name: str = "common") -> None:
    """Register all DMA/IOMMU rules into *registry*."""
    registry.register(DMA001MissingIommuBinding(), soc_name)
    registry.register(DMA002IommuPhandelUndefined(), soc_name)


__all__ = [
    "DMA001MissingIommuBinding",
    "DMA002IommuPhandelUndefined",
    "register_iommu_rules",
]
