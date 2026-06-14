"""Allwinner IOMMU rules (AW-3xx series).

Allwinner sun50i-class SoCs (H6, H616/H618, A64-derived, A523/A527) carry a
single ``allwinner,sun50i-*-iommu`` controller that protects the multimedia
masters — Display Engine, Video Engine, GPU, and the camera CSI path.  Each
master is bound with a *master ID* cell: ``iommus = <&iommu ID>``.

The common ``DMA-001`` rule checks that a DMA master *has* an ``iommus``
binding.  This vendor rule is complementary: it checks that the bindings that
*are* present use distinct master IDs.

AW-301  IOMMU master-ID collision
    Two devices bound to the same ``<&iommu ID>`` share one translation
    context.  They can read and write each other's DMA buffers — the IOMMU
    isolation the binding was meant to provide is silently defeated, and a
    page-table update for one master corrupts the other.
"""

from typing import Dict, List, Tuple

from socc.model import SoC, Violation
from ..base import BaseRule, CheckContext


def _compat_of(node) -> str:
    val = node.properties.get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


def _iommu_bindings(value) -> List[Tuple[str, int]]:
    """Extract (controller_phandle, master_id) pairs from an ``iommus`` value.

    The parser flattens ``iommus = <&iommu 0>, <&iommu 5>`` to
    ``['&iommu', 0, '&iommu', 5]``.  sun50i-iommu uses ``#iommu-cells = <1>``,
    so each phandle is followed by exactly one master-ID integer.
    """
    if not isinstance(value, (list, tuple)):
        return []
    pairs: List[Tuple[str, int]] = []
    current = None
    for item in value:
        if isinstance(item, str) and item.startswith("&"):
            current = item
        elif isinstance(item, int) and current is not None:
            pairs.append((current, item))
            current = None
    return pairs


class AW301IommuMasterIdCollision(BaseRule):
    """AW-301: two masters bound to the same IOMMU master ID."""

    code = "AW-301"
    name = "IOMMU Master-ID Collision"
    description = (
        "Each device bound to the Allwinner IOMMU must use a unique master-ID "
        "cell.  Two masters sharing an ID share one translation context, "
        "defeating IOMMU isolation."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []
        if not model.devices:
            return violations

        # (controller, master_id) -> [device names]
        usage: Dict[Tuple[str, int], List[str]] = {}
        for dev_name, dev_node in model.devices.items():
            for ctrl, mid in _iommu_bindings(dev_node.properties.get("iommus")):
                usage.setdefault((ctrl, mid), []).append(dev_name)

        for (ctrl, mid), devs in usage.items():
            if len(devs) > 1:
                violations.append(self._create_violation(
                    message=(
                        f"IOMMU master ID {mid} on {ctrl} is shared by "
                        f"{len(devs)} devices: {', '.join(sorted(devs))}."
                    ),
                    impact=(
                        "The colliding masters land in the same IOMMU "
                        "translation context: they can access each other's DMA "
                        "buffers and a page-table update for one corrupts the "
                        "other.  The isolation the iommus binding promised is "
                        "silently lost."
                    ),
                    suggestion=(
                        "Assign a distinct master-ID cell to each device, e.g. "
                        f"`iommus = <{ctrl} 0>;` and `iommus = <{ctrl} 1>;`.  "
                        "Master IDs are defined by the SoC IOMMU wiring — see the "
                        "vendor IOMMU binding for the correct per-engine values."
                    ),
                    location=f"/{sorted(devs)[0]}",
                    affected_nodes=sorted(devs),
                ))

        return violations


def register_allwinner_iommu_rules(registry, soc_name: str) -> None:
    """Register Allwinner IOMMU rules."""
    registry.register(AW301IommuMasterIdCollision(), soc_name)
