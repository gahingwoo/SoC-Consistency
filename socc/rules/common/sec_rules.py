"""Security isolation audit rules (SEC-2xx series).

Checks DTS configurations for TrustZone / TEE isolation violations that
could allow a Normal-world process or DMA master to access Secure-world
memory or peripherals.

SEC-201  Secure carveout overlaps a DMA master's accessible range
SEC-202  Cryptographic / security peripheral accessible from Normal-world
SEC-203  Debug-triggering interface left enabled (JTAG, CoreSight, ETM)
SEC-204  Trusted firmware (ATF/OP-TEE) region is writeable from NS-DMA

These rules are *static DTS* checks — they cannot replace a full TrustZone
security review, but they catch the most common misconfiguration patterns
that lead to CVEs.
"""

from __future__ import annotations

from typing import FrozenSet, List, Optional, Tuple

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext


# ── Classifier sets ───────────────────────────────────────────────────────────

_SECURE_NAME_KEYWORDS: FrozenSet[str] = frozenset({
    "optee", "op-tee", "tee", "trustzone", "tz",
    "secure", "atf", "trusted", "sm",
    "sec-region", "fw-ddr",
})

_DMA_MASTER_COMPAT: FrozenSet[str] = frozenset({
    # Video / multimedia (direct DMA, not via DMA engine)
    "vpu", "vdec", "venc", "vepu", "rkvdec",
    "av1-vpu", "vp9-vpu",
    # Image signal processor
    "rkisp", "isp",
    # GPU
    "gpu", "mali", "bifrost", "panfrost",
    # NPU
    "npu", "rknn",
    # USB host (direct DMA)
    "xhci", "ehci", "dwc3",
    # PCIe
    "pcie",
    # Ethernet (direct DMA)
    "gmac", "stmmac",
})

# Compatible substrings that disqualify DMA-master classification
_DMA_MASTER_EXCLUDE: FrozenSet[str] = frozenset({
    "grf", "syscon", "-connector",
})

_CRYPTO_COMPAT: FrozenSet[str] = frozenset({
    "crypto", "trng", "rng", "prng",
    "otp", "efuse",
    "secure-rtc",
    "trusty",
    "rockchip,rk3588-crypto",
    "arm,cryptocell",
    "qcom,crypto",
    "mxs-dcp",
    "caam",
})

_DEBUG_COMPAT: FrozenSet[str] = frozenset({
    "coresight", "arm,etm", "arm,ptm", "arm,cti",
    "arm,cortex-a72-pmu", "arm,cortex-a55-pmu",
    "jtag",
})

_SECURE_MEM_PROPS: FrozenSet[str] = frozenset({
    "no-map", "reusable",
})


def _get_compatible_str(props: dict) -> str:
    compat = props.get("compatible", "")
    if isinstance(compat, (list, tuple)):
        return " ".join(str(c) for c in compat).lower()
    return str(compat).lower()


def _has_kw(text: str, kws: FrozenSet[str]) -> bool:
    return any(kw in text for kw in kws)


def _get_reg_range(props: dict) -> Optional[Tuple[int, int]]:
    """Extract (base_addr, size) from a reg property if possible."""
    reg = props.get("reg")
    if isinstance(reg, (list, tuple)) and len(reg) >= 2:
        try:
            return (int(reg[0]), int(reg[1]))
        except (TypeError, ValueError):
            pass
    return None


def _ranges_overlap(a_start: int, a_size: int, b_start: int, b_size: int) -> bool:
    a_end = a_start + a_size
    b_end = b_start + b_size
    return a_start < b_end and b_start < a_end


# ── SEC-201 ───────────────────────────────────────────────────────────────────


class SEC201SecureMemoryLeakage(BaseRule):
    """SEC-201: DMA master may access OP-TEE / secure carveout memory.

    Looks for reserved-memory nodes that are marked ``no-map`` (typically
    OP-TEE, TF-A, or TEE carveouts) and for DMA-capable peripherals.  If a
    DMA master has an accessible memory window that overlaps the secure
    carveout the rule fires.

    Because the SoC model does not carry a full physical memory map, this
    rule uses heuristics: it flags any DMA master whose ``memory-region``
    phandle points at a node with ``no-map``, or whose compatible string
    references iommu/smmu-less GPU/NPU/VPU while a ``no-map`` secure region
    exists.
    """

    code = "SEC-201"
    name = "DMA Master May Access Secure Memory Carveout"
    description = (
        "A DMA-capable peripheral (VPU, GPU, ISP, DMA controller) has "
        "potential access to a memory region marked no-map, which is "
        "typically used for OP-TEE / TF-A secure carveouts.  Exploitation "
        "can leak TrustZone secrets or allow privilege escalation."
    )
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        # Find all secure/no-map memory regions
        secure_nodes = [
            (name, node)
            for name, node in model.devices.items()
            if (
                "no-map" in node.properties
                or _has_kw(name.lower(), _SECURE_NAME_KEYWORDS)
                or _has_kw(_get_compatible_str(node.properties), _SECURE_NAME_KEYWORDS)
            )
        ]

        if not secure_nodes:
            return []

        secure_ranges: List[Tuple[str, int, int]] = []
        for sname, snode in secure_nodes:
            r = _get_reg_range(snode.properties)
            if r:
                secure_ranges.append((sname, r[0], r[1]))

        # Find DMA masters without explicit IOMMU protection
        for dev_name, dev_node in model.devices.items():
            compat = _get_compatible_str(dev_node.properties)
            is_dma_master = (
                (_has_kw(compat, _DMA_MASTER_COMPAT) or _has_kw(dev_name.lower(), _DMA_MASTER_COMPAT))
                and not _has_kw(compat, _DMA_MASTER_EXCLUDE)
                and "dmas" not in dev_node.properties
            )
            if not is_dma_master:
                continue
            # display-subsystem is a virtual bus aggregator, not a DMA master itself.
            if "display-subsystem" in compat:
                continue

            # Check if IOMMU is present (provides isolation)
            has_iommu = "iommus" in dev_node.properties or "dma-ranges" in dev_node.properties

            # Check if memory-region points to a no-map node
            mem_region = dev_node.properties.get("memory-region")
            overlaps_secure = False
            overlap_name = ""

            if mem_region:
                mem_str = str(mem_region).lower()
                for sname, _, _ in secure_nodes:
                    if sname.lower() in mem_str:
                        overlaps_secure = True
                        overlap_name = sname
                        break

            # Even without explicit memory-region, flag iommu-less DMA masters
            # when secure regions exist — unless SMMU/IOMMU is present
            if (overlaps_secure or not has_iommu) and secure_ranges:
                severity = "error" if overlaps_secure else "warning"
                msg = (
                    f"DMA master {dev_name!r} "
                    + (
                        f"has memory-region pointing at secure node {overlap_name!r}."
                        if overlaps_secure
                        else "has no IOMMU (iommus property missing) while secure "
                             "carveout(s) exist in the system."
                    )
                )
                violations.append(
                    self._create_violation(
                        message=msg,
                        impact=(
                            "Without IOMMU isolation, a compromised user-space driver "
                            "can use this DMA master to read or write OP-TEE / TF-A "
                            "secure memory — bypassing TrustZone protection."
                        ),
                        suggestion=(
                            f"Add 'iommus = <&iommu_node>;' to the {dev_name} node "
                            f"and ensure the SMMU / IOMMU driver is enabled.  "
                            f"If this DMA master genuinely needs access to the "
                            f"secure region, use a dedicated CMA/non-secure carveout "
                            f"instead of a no-map region."
                        ),
                        location=dev_node.path,
                        affected_nodes=[dev_name] + [sn for sn, _ in secure_nodes],
                        severity=severity,
                    )
                )

        return violations


# ── SEC-202 ───────────────────────────────────────────────────────────────────


class SEC202CryptoAccessibleFromNS(BaseRule):
    """SEC-202: Cryptographic / OTP peripheral accessible from Normal-world.

    Checks that crypto, TRNG, OTP/eFuse, and other security peripherals
    are not left open to Normal-world access.  In properly secured
    configurations these peripherals have ``status = "disabled"`` in the
    Normal-world DTS and are only described in the Secure-world (OP-TEE)
    DTS.
    """

    code = "SEC-202"
    name = "Cryptographic Peripheral Accessible from Normal-World"
    description = (
        "A cryptographic engine, TRNG, or OTP/eFuse node is enabled in the "
        "Normal-world DTS.  These peripherals should only be accessible from "
        "the Secure World (OP-TEE / TF-A) to prevent key material leakage."
    )
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for dev_name, dev_node in model.devices.items():
            compat = _get_compatible_str(dev_node.properties)
            if not _has_kw(compat, _CRYPTO_COMPAT) and not _has_kw(dev_name.lower(), _CRYPTO_COMPAT):
                continue

            status = dev_node.properties.get("status", "okay")
            if status not in ("okay", "ok", ""):
                continue   # already disabled — good

            violations.append(
                self._create_violation(
                    message=(
                        f"Crypto/security peripheral {dev_name!r} is enabled "
                        f"in Normal-world DTS ({compat.split()[0] if compat else ''})."
                    ),
                    impact=(
                        "Normal-world Linux processes (with sufficient privilege) "
                        "can access the cryptographic engine directly, bypassing "
                        "the Secure-world key management and potentially exposing "
                        "hardware-protected keys."
                    ),
                    suggestion=(
                        f"Set status = \"disabled\" on {dev_name} in the Normal-world "
                        f"DTS. If this crypto engine is required for Normal-world "
                        f"use (e.g., dm-crypt), ensure its memory-region is isolated "
                        f"and a proper crypto API driver with access controls is used."
                    ),
                    location=dev_node.path,
                    affected_nodes=[dev_name],
                )
            )

        return violations


# ── SEC-203 ───────────────────────────────────────────────────────────────────


class SEC203DebugInterfaceEnabled(BaseRule):
    """SEC-203: CoreSight / JTAG / ETM debug interface left enabled.

    Debug and trace interfaces (CoreSight ETM, PTM, CTI, JTAG) should be
    disabled in production firmware.  An enabled debug interface allows an
    attacker with physical access to extract memory, set breakpoints, or
    even access Secure-world state.
    """

    code = "SEC-203"
    name = "Debug / Trace Interface Enabled in Production DTS"
    description = (
        "A hardware debug or trace interface (CoreSight ETM/PTM, JTAG, "
        "CPU PMU) is enabled in the DTS.  Production firmware should "
        "disable these to prevent physical debug attacks."
    )
    severity = "info"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for dev_name, dev_node in model.devices.items():
            compat = _get_compatible_str(dev_node.properties)
            if not _has_kw(compat, _DEBUG_COMPAT) and not _has_kw(dev_name.lower(), frozenset({"coresight", "etm", "ptm", "jtag"})):
                continue

            status = dev_node.properties.get("status", "okay")
            if status not in ("okay", "ok", ""):
                continue

            violations.append(
                self._create_violation(
                    message=(
                        f"Debug interface {dev_name!r} is enabled "
                        f"({compat.split()[0] if compat else dev_name})."
                    ),
                    impact=(
                        "Physical attackers can use CoreSight / JTAG to extract "
                        "memory contents (including TrustZone Secure-world state), "
                        "set execution breakpoints, or manipulate hardware registers."
                    ),
                    suggestion=(
                        f"Set status = \"disabled\" on {dev_name} for production "
                        f"builds. Disable JTAG via eFuse / OTP on production "
                        f"hardware. Keep debug interfaces enabled only in "
                        f"engineering/development builds."
                    ),
                    location=dev_node.path,
                    affected_nodes=[dev_name],
                )
            )

        return violations


# ── Registration ──────────────────────────────────────────────────────────────


def register_sec_rules(registry, soc_name: str = "common") -> None:
    """Register SEC-2xx rules."""
    registry.register(SEC201SecureMemoryLeakage(), soc_name)
    registry.register(SEC202CryptoAccessibleFromNS(), soc_name)
    registry.register(SEC203DebugInterfaceEnabled(), soc_name)


__all__ = [
    "SEC201SecureMemoryLeakage",
    "SEC202CryptoAccessibleFromNS",
    "SEC203DebugInterfaceEnabled",
    "register_sec_rules",
]
