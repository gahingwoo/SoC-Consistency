"""
Kconfig / DTS Bridge — "Driver Amnesia Detector"

Parses the Linux kernel .config file and cross-references it against
DTS nodes that have status="okay".  Any device whose `compatible` string
maps to a kernel driver that is NOT compiled in (or NOT a module) is
flagged — the hardware is powered on but the kernel can never talk to it.

Built-in compatible → CONFIG mapping database covers ~80 common drivers.
Users can extend it by passing --compat-db my_mappings.yaml.

CLI entry:
  socc audit-kernel --config .config board.dts
  socc audit-kernel --config /path/to/.config board.dts --compat-db extra.yaml
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from socc.model import SoC


# ─────────────────────────────────────────────────────────────────────────────
# Built-in compatible → CONFIG mapping
# ─────────────────────────────────────────────────────────────────────────────

# Format: "compatible-string": ("CONFIG_XXX", "human description")
_COMPAT_DB: Dict[str, Tuple[str, str]] = {
    # I2C
    "snps,dw-apb-i2c":                   ("CONFIG_I2C_DESIGNWARE_PLATFORM", "Synopsys DesignWare APB I2C"),
    "rockchip,rk3399-i2c":               ("CONFIG_I2C_DESIGNWARE_PLATFORM", "Rockchip RK3399 I2C"),

    # SPI
    "rockchip,rk3588-spi":               ("CONFIG_SPI_ROCKCHIP", "Rockchip SPI controller"),
    "arm,pl022":                          ("CONFIG_SPI_PL022", "ARM PL022 SSP"),
    "snps,dw-apb-ssp":                   ("CONFIG_SPI_DW", "Synopsys DesignWare SPI"),

    # UART / Serial
    "snps,dw-apb-uart":                  ("CONFIG_SERIAL_8250_DW", "Synopsys DesignWare UART"),
    "arm,pl011":                          ("CONFIG_SERIAL_AMBA_PL011", "ARM PrimeCell UART PL011"),
    "ns16550":                            ("CONFIG_SERIAL_8250", "National Semiconductor 16550A UART"),

    # GPIO
    "rockchip,gpio-bank":                ("CONFIG_PINCTRL_ROCKCHIP", "Rockchip GPIO/pinctrl"),
    "gpio-keys":                         ("CONFIG_KEYBOARD_GPIO", "GPIO-connected keyboard/buttons"),

    # Ethernet
    "rockchip,rk3588-gmac":             ("CONFIG_ROCKCHIP_GMAC", "Rockchip GMAC Ethernet"),
    "snps,dwmac-5.20":                   ("CONFIG_STMMAC_ETH", "Synopsys STMMAC Ethernet"),
    "realtek,rtl8111":                   ("CONFIG_R8169", "Realtek RTL8111 NIC"),

    # PCIe
    "rockchip,rk3588-pcie3":            ("CONFIG_PCIE_ROCKCHIP_DW_HOST", "Rockchip RK3588 PCIe3"),
    "snps,dw-pcie":                      ("CONFIG_PCIE_DW", "Synopsys DesignWare PCIe"),

    # USB
    "rockchip,rk3588-dwc3":             ("CONFIG_USB_DWC3", "Synopsys DWC3 USB"),
    "snps,dwc3":                         ("CONFIG_USB_DWC3", "Synopsys DWC3 USB3.0"),
    "generic-ohci":                      ("CONFIG_USB_OHCI_HCD", "Generic OHCI USB host"),
    "generic-ehci":                      ("CONFIG_USB_EHCI_HCD", "Generic EHCI USB host"),

    # MMC / Storage
    "rockchip,rk3588-dw-mshc":         ("CONFIG_MMC_DW_ROCKCHIP", "Rockchip SD/MMC host"),
    "arasan,sdhci-8.9a":                 ("CONFIG_MMC_SDHCI_ARASAN", "Arasan SDHCI"),
    "mmc-spi-slot":                      ("CONFIG_MMC_SPI", "MMC over SPI"),

    # Display / GPU
    "rockchip,rk3588-vop":              ("CONFIG_DRM_ROCKCHIP", "Rockchip VOP display"),
    "rockchip,rk3588-mali":             ("CONFIG_DRM_PANFROST", "Mali Valhall GPU (Panfrost)"),
    "arm,mali-valhall":                 ("CONFIG_DRM_PANFROST", "Mali Valhall GPU"),
    "rockchip,rk3399-mali":             ("CONFIG_DRM_PANFROST", "Mali T860/G72 GPU"),
    "panel-simple":                     ("CONFIG_DRM_PANEL_SIMPLE", "Simple DRM panel"),

    # Interrupt controller
    "arm,gic-v3":                        ("CONFIG_ARM_GIC_V3", "ARM GICv3 interrupt controller"),
    "arm,gic-v2":                        ("CONFIG_ARM_GIC", "ARM GICv2 interrupt controller"),
    "arm,cortex-a76":                    ("CONFIG_ARM64", "ARM Cortex-A76 CPU"),

    # Clock
    "rockchip,rk3588-cru":              ("CONFIG_CLK_RK3588", "Rockchip RK3588 clock driver"),
    "rockchip,rk3588s-cru":             ("CONFIG_CLK_RK3588", "Rockchip RK3588S clock driver"),

    # PMIC / Regulators
    "rockchip,rk806":                    ("CONFIG_MFD_RK8XX", "Rockchip RK806 PMIC"),
    "rockchip,rk817":                    ("CONFIG_MFD_RK8XX", "Rockchip RK817 PMIC"),
    "rockchip,rk818":                    ("CONFIG_MFD_RK8XX", "Rockchip RK818 PMIC"),
    "ti,tps65132":                       ("CONFIG_REGULATOR_TPS65132", "TI TPS65132 regulator"),

    # Thermal
    "rockchip,thermal":                  ("CONFIG_ROCKCHIP_THERMAL", "Rockchip thermal sensor"),
    "arm,scmi-thermal":                  ("CONFIG_ARM_SCMI_THERMAL_DRIVER", "SCMI thermal"),

    # Watchdog
    "snps,dw-wdt":                       ("CONFIG_DW_WATCHDOG", "Synopsys DesignWare WDT"),
    "arm,sbsa-gwdt":                     ("CONFIG_ARM_SBSA_WATCHDOG", "SBSA generic watchdog"),

    # RTC
    "rockchip,rk817-rtc":               ("CONFIG_RTC_DRV_RK808", "Rockchip RK817 RTC"),
    "haoyu,hym8563":                     ("CONFIG_RTC_DRV_HYM8563", "HYM8563 RTC"),
    "nxp,pcf85263":                      ("CONFIG_RTC_DRV_PCF85363", "NXP PCF85263 RTC"),

    # NPU / ML
    "rockchip,rk3588-rknn":             ("CONFIG_ROCKCHIP_RKNPU", "Rockchip RKNN NPU"),

    # CAN bus
    "bosch,m_can":                       ("CONFIG_CAN_M_CAN", "Bosch M_CAN CAN controller"),
    "nxp,flexcan":                       ("CONFIG_CAN_FLEXCAN", "NXP FlexCAN"),
    "microchip,mcp2515":                 ("CONFIG_CAN_MCP251X", "Microchip MCP2515 CAN"),

    # NXP i.MX
    "fsl,imx8mp-i2c":                   ("CONFIG_I2C_IMX", "NXP i.MX I2C"),
    "fsl,imx8mp-uart":                   ("CONFIG_SERIAL_IMX", "NXP i.MX UART"),
    "fsl,imx8mp-usdhc":                  ("CONFIG_MMC_SDHCI_ESDHC_IMX", "NXP i.MX uSDHC"),

    # Qualcomm
    "qcom,geni-i2c":                     ("CONFIG_I2C_QCOM_GENI", "Qualcomm GENI I2C"),
    "qcom,msm-serial":                   ("CONFIG_SERIAL_MSM", "Qualcomm MSM UART"),
}


# ─────────────────────────────────────────────────────────────────────────────
# .config parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_kconfig(config_path: str) -> Dict[str, str]:
    """
    Parse a Linux .config file and return {CONFIG_XXX: "y" | "m" | "n"}.
    Lines that are commented out appear as "n".
    """
    config: Dict[str, str] = {}
    for line in Path(config_path).read_text(errors="replace").splitlines():
        line = line.strip()
        # "# CONFIG_XXX is not set"
        m = re.match(r"#\s*(CONFIG_\w+)\s+is not set", line)
        if m:
            config[m.group(1)] = "n"
            continue
        # "CONFIG_XXX=y" or "CONFIG_XXX=m" or "CONFIG_XXX="..."
        m = re.match(r"(CONFIG_\w+)=(.+)", line)
        if m:
            config[m.group(1)] = m.group(2).strip('"')
    return config


# ─────────────────────────────────────────────────────────────────────────────
# Extension database loader
# ─────────────────────────────────────────────────────────────────────────────

def load_compat_db_extension(path: str) -> Dict[str, Tuple[str, str]]:
    """Load extra compatible → CONFIG mappings from a YAML or JSON file."""
    ext: Dict[str, Tuple[str, str]] = {}
    text = Path(path).read_text()

    try:
        import yaml
        raw = yaml.safe_load(text) or {}
    except Exception:
        import json
        raw = json.loads(text)

    for compat, info in raw.items():
        if isinstance(info, dict):
            cfg = info.get("config", info.get("kernel_config", ""))
            desc = info.get("description", "")
            ext[str(compat)] = (str(cfg), str(desc))
        elif isinstance(info, str):
            ext[str(compat)] = (info, "")

    return ext


# ─────────────────────────────────────────────────────────────────────────────
# Audit engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KernelAuditResult:
    severity: str           # "ERROR" | "WARNING" | "INFO"
    node_path: str
    node_name: str
    compatible: str
    required_config: str
    config_state: str       # "y" | "m" | "n" | "missing"
    description: str
    fix: str = ""


def audit_kernel_config(
    soc: SoC,
    kconfig: Dict[str, str],
    extra_db: Optional[Dict[str, Tuple[str, str]]] = None,
) -> List[KernelAuditResult]:
    """
    Cross-reference DTS nodes against kernel .config.
    Returns a list of KernelAuditResult for every mismatch found.
    """
    db = dict(_COMPAT_DB)
    if extra_db:
        db.update(extra_db)

    results: List[KernelAuditResult] = []
    reported: Set[str] = set()  # deduplicate by (config, node)

    for dev_name, node in soc.devices.items():
        # Only check enabled nodes
        status = node.properties.get("status", "okay")
        if status not in ("okay", "ok"):
            continue

        compat_list = node.properties.get("compatible") or []
        if isinstance(compat_list, str):
            compat_list = [compat_list]

        for compat in compat_list:
            if compat not in db:
                continue
            cfg_name, desc = db[compat]
            if not cfg_name:
                continue
            key = f"{cfg_name}::{dev_name}"
            if key in reported:
                continue
            reported.add(key)

            state = kconfig.get(cfg_name, "missing")

            if state in ("y", "1"):
                continue  # built-in — all good
            elif state == "m":
                results.append(KernelAuditResult(
                    severity="WARNING",
                    node_path=node.path,
                    node_name=dev_name,
                    compatible=compat,
                    required_config=cfg_name,
                    config_state="m",
                    description=(
                        f"Driver '{desc}' ({cfg_name}=m) is a loadable module. "
                        "If the module is not included in the initrd/rootfs, "
                        "the device will not probe at boot."
                    ),
                    fix=(
                        f"Either change {cfg_name}=y in .config (built-in), "
                        "or ensure the module is loaded early in initramfs."
                    ),
                ))
            else:
                # "n" or "missing" — driver not compiled
                action = (
                    "Run `make menuconfig` and enable" if state == "n"
                    else "Add"
                )
                results.append(KernelAuditResult(
                    severity="ERROR",
                    node_path=node.path,
                    node_name=dev_name,
                    compatible=compat,
                    required_config=cfg_name,
                    config_state=state,
                    description=(
                        f"Hardware node '{dev_name}' (compatible=\"{compat}\") "
                        f"is enabled in DTS, but the kernel driver {cfg_name} "
                        f"is NOT compiled in (state: {state})."
                    ),
                    fix=(
                        f"{action} {cfg_name} in your kernel .config. "
                        f"This enables the '{desc}' driver."
                    ),
                ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KernelAuditReport:
    dts_path: str
    config_path: str
    results: List[KernelAuditResult] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.severity == "WARNING")

    @property
    def is_clean(self) -> bool:
        return self.error_count == 0


def run_kernel_audit(
    soc: SoC,
    config_path: str,
    dts_path: str = "board.dts",
    extra_db_path: Optional[str] = None,
) -> KernelAuditReport:
    kconfig = parse_kconfig(config_path)
    extra = load_compat_db_extension(extra_db_path) if extra_db_path else None
    results = audit_kernel_config(soc, kconfig, extra)
    return KernelAuditReport(dts_path=dts_path, config_path=config_path, results=results)


# ─────────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────────

_COLORS = {
    "ERROR":   "\033[1;31m",
    "WARNING": "\033[1;33m",
    "INFO":    "\033[1;36m",
    "RESET":   "\033[0m",
}


def render_kernel_audit(report: KernelAuditReport, use_color: bool = True) -> str:
    lines: List[str] = []

    def c(sev: str, text: str) -> str:
        return f"{_COLORS.get(sev, '')}{text}{_COLORS['RESET']}" if use_color else text

    lines.append("=" * 70)
    lines.append("  SOCC KCONFIG / DTS BRIDGE AUDIT")
    lines.append(f"  DTS    : {report.dts_path}")
    lines.append(f"  Config : {report.config_path}")
    lines.append("=" * 70)

    if not report.results:
        lines.append(c("INFO", "\n[✓] All DTS-enabled devices have matching kernel drivers compiled in."))
    else:
        for r in report.results:
            lines.append("")
            lines.append(c(r.severity, f"[KERNEL MISMATCH {r.severity}]"))
            lines.append(f"  Hardware Node : {r.node_path} ({r.node_name})")
            lines.append(f"  Compatible    : {r.compatible}")
            lines.append(f"  Kernel Config : {r.required_config} = {r.config_state}")
            lines.append(f"  Impact        : {r.description}")
            lines.append(f"  Fix           : {r.fix}")

    lines.append("")
    lines.append(
        f"Summary: {report.error_count} error(s), {report.warning_count} warning(s)."
    )
    return "\n".join(lines)
