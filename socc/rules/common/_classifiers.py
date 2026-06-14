"""Shared device classifiers for the common rule set.

``iommu_rules`` and ``sec_rules`` both need to answer the same two questions:

  * Is this node a *direct* DMA bus master (and therefore subject to IOMMU
    isolation / capable of reaching a secure carveout)?
  * Is this node an IOMMU / SMMU controller?

Historically each module carried its own near-identical copy of the token
sets and helper predicates, which drifted over time (one gained ``mipi-csi``
and ``dwc2``, the other did not).  Centralising them here keeps the two rules
in lock-step: a new DMA-capable IP block is recognised by *both* the isolation
rule and the secure-memory rule the moment it is added to ``DMA_MASTER_TOKENS``.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

# ── DMA bus masters ──────────────────────────────────────────────────────────
# IP blocks that perform DMA *directly* (not via the external DMA-engine
# subsystem) and therefore (a) require their own ``iommus`` group and
# (b) can independently reach physical memory — including secure carveouts.
DMA_MASTER_TOKENS: FrozenSet[str] = frozenset({
    # GPU
    "gpu", "mali", "bifrost", "panfrost", "valhall",
    # Video codec (integrated DMA engines, NOT pl330 clients)
    "vpu", "vdec", "venc", "vepu", "rkvdec", "rkvenc",
    # AV1 / VP9 hardware decoder
    "av1-vpu", "vp9-vpu",
    # Image signal processor
    "rkisp", "isp",
    # NPU / ML accelerator
    "npu", "rknn", "rknn-core",
    # USB host (XHCI/EHCI/DWC3/DWC2 perform DMA directly)
    "xhci", "ehci", "dwc3", "dwc2",
    # PCIe root complex (DMA peer-to-peer)
    "pcie",
    # Ethernet MAC with integrated DMA
    "gmac", "stmmac",
    # Camera / MIPI-CSI DMA path
    "mipi-csi", "csi2",
})

# Compatible substrings that disqualify a DMA-master match even when a token
# above is present (config syscons, connector stubs).
DMA_MASTER_EXCLUDE: FrozenSet[str] = frozenset({
    "grf",        # Rockchip GRF (general register file) — config syscon
    "syscon",     # Generic system controller — no DMA
    "-connector", # Connector stubs (hdmi-connector, etc.)
})

# DMA-engine controller nodes.  These are infrastructure (they hold an IOMMU
# group on behalf of their clients); the IOMMU rule treats them as non-clients,
# whereas the security rule still regards them as bus masters that can touch
# secure memory.
DMA_ENGINE_CONTROLLER_TOKENS: FrozenSet[str] = frozenset({
    "pl330", "axi-dmac",
})

# ── IOMMU / SMMU controllers ──────────────────────────────────────────────────
IOMMU_CONTROLLER_TOKENS: FrozenSet[str] = frozenset({
    "iommu", "smmu", "iommu-v1", "iommu-v2",
    "rockchip,iommu", "arm,smmu", "arm,mmu-500",
    "qcom,iommu", "qcom,smmu-500",
    "fsl,imx8mp-iommu", "fsl,imx-iommu",
    "allwinner,sun50i-iommu",
})


# ── helpers ───────────────────────────────────────────────────────────────────

def compat_str(props: Dict[str, Any]) -> str:
    """Return the node's ``compatible`` property as a single lower-cased string."""
    val = props.get("compatible", "")
    if isinstance(val, (list, tuple)):
        return " ".join(str(v) for v in val).lower()
    return str(val).lower()


def is_iommu_controller(compat: str) -> bool:
    """Return True if *compat* identifies an IOMMU / SMMU controller."""
    return any(tok in compat for tok in IOMMU_CONTROLLER_TOKENS)


def is_dma_master(
    compat: str,
    props: Optional[Dict[str, Any]] = None,
    *,
    name: str = "",
    match_name: bool = False,
    exclude_dma_engines: bool = True,
) -> bool:
    """Return True when a node is a direct DMA bus master.

    Args:
        compat: lower-cased ``compatible`` string (see :func:`compat_str`).
        props: node properties; when given, a node declaring ``dmas`` is treated
            as a DMA-engine *client* (the engine owns the IOMMU group) and
            excluded.
        name: node name, consulted only when *match_name* is True.
        match_name: also match :data:`DMA_MASTER_TOKENS` against *name*.  The
            security rule relies on this to catch nodes whose master role shows
            up in the node label rather than the compatible string.
        exclude_dma_engines: when True (the IOMMU rule), treat ``pl330`` /
            ``axi-dmac`` controller nodes as infrastructure rather than clients.
            The security rule passes False because a DMA engine can itself reach
            secure memory.
    """
    if any(exc in compat for exc in DMA_MASTER_EXCLUDE):
        return False
    if exclude_dma_engines and any(tok in compat for tok in DMA_ENGINE_CONTROLLER_TOKENS):
        return False
    if props is not None and "dmas" in props:
        return False
    if any(tok in compat for tok in DMA_MASTER_TOKENS):
        return True
    if match_name and name and any(tok in name.lower() for tok in DMA_MASTER_TOKENS):
        return True
    return False
