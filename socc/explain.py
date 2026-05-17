"""Datasheet Reference Mapper — "socc explain".

``socc explain /soc/i2c@fe580000``
``socc explain pmic board.dts``
``socc explain --list`` (dump the full knowledge base)

For each DTS node or compatible string the tool outputs:

  • Human-readable hardware-block description
  • What each clock signal does
  • What each interrupt means
  • Where to find the relevant chapter in the official TRM / datasheet
  • Common configuration pitfalls

────────────────────────────────────────────────────────────────────────────
Design philosophy
────────────────────────────────────────────────────────────────────────────
This tool is a "senior engineer in your pocket".  It does not enforce
anything; it simply explains.  The knowledge base is built from publicly
available Rockchip/ARM binding documentation and is intentionally legible
by students with no prior DTS experience.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from socc.model import SoC


# ── Knowledge Base ────────────────────────────────────────────────────────────
# Each entry maps a compatible string prefix (or keyword) to documentation.

@dataclass
class HWBlockDoc:
    block_name: str               # e.g. "I2C Controller (DesignWare APB)"
    description: str              # 2-4 sentence explanation
    clock_docs: Dict[str, str]    # clock-name → what it does
    irq_doc: str                  # what the interrupt signals
    trm_refs: List[str]           # e.g. ["RK3588 TRM Part 1, Chapter 9 (I2C)"]
    pitfalls: List[str]           # common configuration mistakes
    typical_config: str = ""      # one-liner DTS snippet hint


_KB: Dict[str, HWBlockDoc] = {

    # ── I2C ──────────────────────────────────────────────────────────────────
    "snps,dw-apb-i2c": HWBlockDoc(
        block_name="I2C Controller (DesignWare APB I2C)",
        description=(
            "A Synopsys DesignWare APB I2C master/slave controller embedded in "
            "most Rockchip SoCs.  Supports Standard (100kHz), Fast (400kHz), "
            "Fast+ (1MHz) and High-Speed (3.4MHz) modes.  Each bus is a "
            "separate controller instance with its own register map."
        ),
        clock_docs={
            "i2c":   "Core clock that drives the I2C bit-bang state machine.  "
                     "Must be ≥ 10× the target SCL frequency.  Typically 200 MHz.",
            "pclk":  "APB peripheral bus clock used to access config registers. "
                     "Typically 100 MHz.  Independent of the I2C line speed.",
        },
        irq_doc=(
            "Fires at end of every transfer (byte sent/received), on NACK, "
            "and on bus arbitration loss.  The Linux i2c-designware driver "
            "uses this to advance the transfer state machine."
        ),
        trm_refs=[
            "RK3588 TRM Part 1, Chapter 9 — I2C",
            "Synopsys DesignWare DW_apb_i2c Databook (search 'DW_apb_i2c')",
            "Linux kernel: drivers/i2c/busses/i2c-designware-*.c",
        ],
        pitfalls=[
            "Missing pull-up resistors on SDA/SCL — typical value is 4.7kΩ "
            "for 100kHz, 2.2kΩ for 400kHz, 1kΩ for 1MHz.",
            "vcc-supply must match the IO voltage of the connected device. "
            "Using a 3.3V supply on a 1.8V bus destroys the SoC IO cell.",
            "i2c-scl-falling-time-ns and i2c-sda-falling-time-ns must be tuned "
            "for the actual pull-up resistor and bus capacitance.",
        ],
        typical_config="clock-frequency = <400000>;  /* 400kHz Fast Mode */",
    ),

    # ── SPI ──────────────────────────────────────────────────────────────────
    "rockchip,rk3588-spi": HWBlockDoc(
        block_name="SPI Controller (Rockchip RK3588)",
        description=(
            "Full-duplex SPI master/slave supporting CPOL/CPHA modes 0–3, "
            "transfer widths of 4–16 bits, and a built-in 64-entry TX/RX FIFO. "
            "Supports DMA-mapped transfers via the PL330 DMA engine."
        ),
        clock_docs={
            "spiclk": "SPI serial clock output.  Divided from CRU to produce "
                      "the actual SCK frequency on the pin.  Max is typically "
                      "spi-max-frequency in DTS.",
            "apb_pclk": "APB register access clock.",
        },
        irq_doc=(
            "Fires on TX FIFO half-empty (ready to send more) and on "
            "RX FIFO half-full (data ready to read).  Also fires on overflow."
        ),
        trm_refs=[
            "RK3588 TRM Part 1, Chapter 12 — SPI",
            "Linux kernel: drivers/spi/spi-rockchip.c",
        ],
        pitfalls=[
            "spi-max-frequency must not exceed 50 MHz for most peripherals; "
            "check the device datasheet's maximum SCLK spec.",
            "CS polarity: most devices expect active-low CS (default); "
            "add 'spi-cs-high;' in the child node for active-high.",
            "DMA channels (dmas, dma-names) must point to the correct "
            "PL330 DMA controller channel pair.",
        ],
    ),

    # ── UART ─────────────────────────────────────────────────────────────────
    "snps,dw-apb-uart": HWBlockDoc(
        block_name="UART Controller (DesignWare APB 16550-compatible)",
        description=(
            "16550A-compatible UART with a Synopsys APB wrapper.  Supports "
            "baud rates from 110 to 4000000, hardware flow control (RTS/CTS), "
            "IrDA SIR mode, and RS-485.  Often used as the kernel's debug console."
        ),
        clock_docs={
            "baudclk":  "Main baud rate clock.  Baud divisor = baudclk / (16 × baud). "
                        "For 1500000 baud at 24MHz: divisor = 1.",
            "apb_pclk": "APB register access clock (usually 100 MHz).",
        },
        irq_doc=(
            "Fires on RX data available, TX FIFO empty, line status error "
            "(framing, parity, overrun, break), and modem status change."
        ),
        trm_refs=[
            "RK3588 TRM Part 1, Chapter 10 — UART",
            "Linux kernel: drivers/tty/serial/8250/8250_dw.c",
        ],
        pitfalls=[
            "earlycon address in kernel cmdline must match the DTS reg base "
            "(e.g. earlycon=uart8250,mmio32,0xfe2b0000).",
            "Clock source: use 'assigned-clocks' and 'assigned-clock-rates' "
            "to lock the UART clock for stable baud rates.",
        ],
        typical_config="current-speed = <1500000>;",
    ),

    # ── GPIO ─────────────────────────────────────────────────────────────────
    "rockchip,gpio-bank": HWBlockDoc(
        block_name="GPIO Bank Controller",
        description=(
            "Each GPIO bank (GPIO0–GPIO4 on RK3588) is a separate controller "
            "with 32 pins (some banks have fewer due to pinout constraints). "
            "Supports input, output, interrupt (edge/level) per pin."
        ),
        clock_docs={
            "bus":  "APB bus clock for register access.  Must be active "
                    "whenever GPIOs are used.",
            "db":   "Debounce clock.  Drives the configurable debounce filter "
                    "for interrupt inputs.  Typically a low-frequency clock "
                    "(e.g. 32 kHz or 24 MHz).",
        },
        irq_doc=(
            "One interrupt per bank.  The GPIO interrupt controller inside "
            "the bank multiplexes all 32 pin interrupts into this single GIC SPI."
        ),
        trm_refs=[
            "RK3588 TRM Part 1, Chapter 6 — GPIO",
            "Linux kernel: drivers/pinctrl/pinctrl-rockchip.c",
        ],
        pitfalls=[
            "Pin function must be configured in pinctrl (iomux) BEFORE the "
            "GPIO controller can use it; forgetting pinctrl-0 is a common bug.",
            "GPIO interrupts require the bank to be powered on at all times "
            "if used as a wake source.",
        ],
    ),

    # ── PMIC / Regulators ─────────────────────────────────────────────────────
    "rockchip,rk806": HWBlockDoc(
        block_name="Power Management IC — RK806",
        description=(
            "The RK806 is a multi-channel PMIC commonly paired with RK3588. "
            "It provides 10 DCDC bucks and 6 LDOs.  Communicates with the "
            "SoC via SPI (not I2C) at up to 10 MHz."
        ),
        clock_docs={},
        irq_doc=(
            "IRQ line signals power-good events, over-temperature, "
            "over-current, and button presses.  The Linux rk806 MFD driver "
            "uses a threaded IRQ handler for each sub-device."
        ),
        trm_refs=[
            "Rockchip RK806 Datasheet (request from Rockchip or find on GitHub)",
            "Linux kernel: drivers/mfd/rk8xx-spi.c",
            "Linux kernel: drivers/regulator/rk8xx-regulator.c",
        ],
        pitfalls=[
            "RK806 connects via SPI, not I2C.  A common mistake is declaring "
            "it as an I2C device.",
            "Power-on sequence: DCDC1 (VDD_CPU_LIT) must be stable before the "
            "CPU cluster starts.  Ensure dvs-gpios and regulators-sequence are set.",
            "DVS (Dynamic Voltage Scaling) GPIO lines must be routed correctly "
            "for CPU DVFS to work.",
        ],
    ),

    "rockchip,rk817": HWBlockDoc(
        block_name="Power Management IC — RK817",
        description=(
            "RK817 is a PMIC+audio codec combo used on RK356x/RK3399 boards. "
            "Provides 4 DCDCs, 7 LDOs, an 8-bit ADC, and a Class-D amplifier. "
            "Uses I2C for configuration."
        ),
        clock_docs={
            "mclk": "Master clock input for the integrated audio codec. "
                    "Typically 12.288 MHz or 11.2896 MHz for audio.",
        },
        irq_doc="Signals power events, PMIC faults, and RTC alarms.",
        trm_refs=[
            "Rockchip RK817 Datasheet",
            "Linux kernel: drivers/mfd/rk8xx-core.c",
        ],
        pitfalls=[
            "Battery charger sub-device requires correct battery capacity and "
            "chemistry parameters (rk817-battery.yaml binding).",
        ],
    ),

    # ── GIC ──────────────────────────────────────────────────────────────────
    "arm,gic-v3": HWBlockDoc(
        block_name="Interrupt Controller — ARM GICv3",
        description=(
            "ARM Generic Interrupt Controller version 3.  Manages all "
            "peripheral interrupts (SPIs), per-CPU interrupts (PPIs), and "
            "inter-processor interrupts (SGIs) for an AArch64 system. "
            "Supports up to 1020 SPIs, 16 SGIs, and 16 PPIs per CPU."
        ),
        clock_docs={},
        irq_doc="N/A — the GIC IS the interrupt controller.",
        trm_refs=[
            "ARM GICv3 Architecture Specification (ARM IHI0069)",
            "RK3588 TRM Part 1, Chapter 3 — Interrupt Controller",
            "Linux kernel: drivers/irqchip/irq-gic-v3.c",
        ],
        pitfalls=[
            "GIC redistributor range must cover one block per CPU core; "
            "the reg property must encode all CPU redistributors.",
            "For KVM/hypervisor: GICC (CPU interface) must be accessible "
            "from EL2.  Use 'its' sub-node for MSI support (PCIe).",
        ],
    ),

    # ── eMMC ─────────────────────────────────────────────────────────────────
    "rockchip,rk3588-dw-mshc": HWBlockDoc(
        block_name="eMMC / SDMMC Controller (DesignWare Mobile Storage HC)",
        description=(
            "Supports eMMC 5.1 HS400 (up to 200MB/s) and SD 3.0 UHS-I. "
            "The RK3588 has a dedicated eMMC controller (EMMC0) separate from "
            "the SDMMC controller used for removable SD cards."
        ),
        clock_docs={
            "biu":     "Bus interface unit clock — APB register access.",
            "ciu":     "Card interface unit clock — drives the eMMC/SD clock pin. "
                       "Up to 200MHz for HS400.",
            "ciu-drive": "Drive-strength tuning clock for HS400 training.",
            "ciu-sample": "Sample-point tuning clock for HS400 training.",
        },
        irq_doc=(
            "Fires on transfer complete, card detection change, "
            "bus error, and DMA completion."
        ),
        trm_refs=[
            "RK3588 TRM Part 2, Chapter 20 — eMMC",
            "Linux kernel: drivers/mmc/host/dw_mmc*.c",
            "JEDEC eMMC 5.1 Standard (JESD84-B51)",
        ],
        pitfalls=[
            "HS400 requires mmc-hs400-1_8v; and careful drive-strength tuning. "
            "Missing tuning causes CRC errors at high speed.",
            "Non-removable eMMC must have 'non-removable;' property to prevent "
            "the kernel from polling for card insertion.",
        ],
    ),

    # ── VOP2 / Display ────────────────────────────────────────────────────────
    "rockchip,rk3588-vop": HWBlockDoc(
        block_name="Video Output Processor 2 (VOP2)",
        description=(
            "The RK3588 VOP2 is a multi-layer video output processor supporting "
            "up to 4 independent display outputs (HDMI, DP, eDP, MIPI-DSI). "
            "Handles compositing, scaling, color space conversion, and gamma "
            "correction at up to 8K@60Hz on the primary output."
        ),
        clock_docs={
            "aclk_vop":  "AXI bus clock for DMA frame buffer access. "
                         "Must be high enough to sustain 4K@60 bandwidth.",
            "hclk_vop":  "AHB register access clock.",
            "dclk_vop0": "Pixel clock for output port 0. Set to h_total × v_total × fps.",
            "dclk_vop1": "Pixel clock for output port 1.",
            "dclk_vop2": "Pixel clock for output port 2.",
            "dclk_vop3": "Pixel clock for output port 3.",
        },
        irq_doc=(
            "Fires on vertical blank (vsync), underrun, and color bar completion. "
            "The DRM/KMS driver uses vsync IRQs for page flipping."
        ),
        trm_refs=[
            "RK3588 TRM Part 2, Chapter 14 — Display System (VOP2)",
            "Linux kernel: drivers/gpu/drm/rockchip/rockchip_drm_vop2.c",
            "DRM/KMS documentation: Documentation/gpu/ in the Linux kernel tree",
        ],
        pitfalls=[
            "Each output encoder (HDMI/DP/DSI) must be routed to a VOP2 port "
            "via the 'ports' node.  Missing port routing causes a blank screen.",
            "DCLK must be set to exactly h_total × v_total × refresh_rate Hz. "
            "Even 1 Hz error causes audio/video sync drift over time.",
            "aclk_vop must provide enough bandwidth: "
            "width × height × bpp × fps / 8 bytes/second.",
        ],
    ),

    # ── PCIe ─────────────────────────────────────────────────────────────────
    "rockchip,rk3588-pcie3": HWBlockDoc(
        block_name="PCIe 3.0 Controller (RK3588)",
        description=(
            "RK3588 includes a PCIe 3.0 x4 root complex (plus two PCIe 2.0 "
            "controllers).  Supports Gen3 (8 GT/s per lane) for NVMe SSDs, "
            "Wi-Fi 6E cards, and 2.5G NICs.  Uses a Synopsys DW PCIe IP core."
        ),
        clock_docs={
            "aclk_pcie":   "AXI master clock for DMA.",
            "aclk_perf_pcie": "Performance counter clock.",
            "pclk_pcie":   "APB config clock.",
            "ref_clk_pcie": "100MHz PCIe reference clock — must be spread-spectrum "
                            "if the endpoint requires it.",
        },
        irq_doc=(
            "One MSI/MSI-X interrupt controller manages all endpoint interrupts. "
            "The host controller also has its own legacy INT A–D interrupts."
        ),
        trm_refs=[
            "RK3588 TRM Part 3, Chapter 30 — PCIe",
            "Linux kernel: drivers/pci/controller/dwc/pcie-dw-rockchip.c",
            "PCI Express Base Specification (pcisig.com)",
        ],
        pitfalls=[
            "reset-gpios must be driven low for ≥ 100ms before PCIe link "
            "training begins.  Insufficient reset time causes link-up failures.",
            "Supply vpcie3v3-supply must be stable before de-asserting reset.",
            "PCIe PHY power: pcie30phy requires its own power supply node.",
        ],
    ),

    # ── USB ───────────────────────────────────────────────────────────────────
    "rockchip,rk3588-dwc3": HWBlockDoc(
        block_name="USB 3.1 / USB 2.0 Controller (DesignWare USB3)",
        description=(
            "Synopsys DesignWare USB 3.1 SuperSpeed controller supporting "
            "both USB 3.1 Gen1 (5 Gbps) and USB 2.0.  RK3588 has two USB3 "
            "controllers paired with Combo PHYs shared with PCIe."
        ),
        clock_docs={
            "ref_clk":  "USB 3.1 reference clock (typically 24 MHz).",
            "suspend_clk": "Always-on 32kHz clock for low-power suspend state.",
            "utmi_clk": "USB 2.0 UTMI clock from the USB2 PHY (60 MHz).",
            "pipe_clk": "USB 3.0 SuperSpeed PIPE clock from the SS PHY.",
        },
        irq_doc=(
            "Single IRQ for all USB events: connect, disconnect, transfer "
            "complete, error.  The DWC3 gadget/host driver demultiplexes."
        ),
        trm_refs=[
            "RK3588 TRM Part 3, Chapter 28 — USB",
            "Linux kernel: drivers/usb/dwc3/",
            "USB 3.1 Specification (usb.org)",
        ],
        pitfalls=[
            "USB Type-C orientation requires CC detection logic (FUSB302 or "
            "similar); without it only one orientation works.",
            "dr_mode = 'otg' requires extcon for VBUS detection; "
            "dr_mode = 'host' or 'peripheral' avoids this complexity.",
        ],
    ),

    # ── Ethernet / GMAC ───────────────────────────────────────────────────────
    "rockchip,rk3588-gmac": HWBlockDoc(
        block_name="Gigabit Ethernet Controller (GMAC)",
        description=(
            "RK3588 integrates two GMAC cores (Synopsys DW Ethernet QoS) "
            "supporting 10/100/1000Mbps via RGMII or RMII.  Each GMAC has an "
            "internal FIFO and DMA engine."
        ),
        clock_docs={
            "stmmaceth": "Core Ethernet clock for the DMA engine.",
            "pclk_gmac": "APB peripheral clock.",
            "aclk_gmac": "AXI DMA clock.",
            "clk_mac_speed": "Speed-select clock — changes between 125/25/2.5 MHz "
                             "for 1G/100M/10M operation.",
        },
        irq_doc=(
            "Single IRQ for TX/RX completion, link state change, "
            "and DMA error events."
        ),
        trm_refs=[
            "RK3588 TRM Part 2, Chapter 22 — GMAC",
            "Linux kernel: drivers/net/ethernet/stmicro/stmmac/",
        ],
        pitfalls=[
            "tx_delay and rx_delay in the DTS must be calibrated for the "
            "specific PHY and PCB trace lengths.  Wrong values cause packet "
            "corruption at 1Gbps.",
            "phy-mode = 'rgmii-id' means delay is in the MAC; "
            "'rgmii-rxid' means delay in PHY for RX only.",
            "pinctrl for GMAC uses high-strength drive (8mA or 12mA slew); "
            "the wrong drive strength causes EMI or signal integrity issues.",
        ],
    ),

    # ── NPU ───────────────────────────────────────────────────────────────────
    "rockchip,rk3588-rknn": HWBlockDoc(
        block_name="Neural Processing Unit (RKNN NPU)",
        description=(
            "RK3588 integrates a 3-core NPU delivering up to 6 TOPS for "
            "inference workloads.  The NPU runs RKNN models compiled from "
            "TensorFlow Lite, ONNX, or PyTorch via the RKNN Toolkit 2."
        ),
        clock_docs={
            "npu_aclk": "NPU AXI master clock for DDR access.",
            "npu_cclk": "NPU compute core clock.  Up to 1.0 GHz.",
        },
        irq_doc="Fires on inference completion and fault events.",
        trm_refs=[
            "RK3588 TRM Part 3, Chapter 33 — NPU",
            "RKNN Toolkit 2 documentation (github.com/airockchip/rknn-toolkit2)",
            "Linux kernel: drivers/rknpu/",
        ],
        pitfalls=[
            "NPU firmware must be loaded before the NPU driver initialises. "
            "Missing firmware causes rknpu probe failure.",
            "NPU DDR bandwidth conflicts with GPU/VOP2; use QoS settings "
            "(npu-qos) to avoid frame drops during concurrent inference.",
        ],
    ),

    # ── CRU / Clock ───────────────────────────────────────────────────────────
    "rockchip,rk3588-cru": HWBlockDoc(
        block_name="Clock & Reset Unit (CRU)",
        description=(
            "The CRU manages all PLL configurations, clock muxes, dividers, "
            "and soft-reset signals for every peripheral in the RK3588.  "
            "The Linux clk driver exposes ~400 clock IDs and ~300 reset IDs."
        ),
        clock_docs={
            "xin24m": "24 MHz crystal reference oscillator — root of all PLLs.",
            "gpll":   "General PLL — typically 1188 MHz.  Feeds most peripherals.",
            "cpll":   "Common PLL — 1500 MHz.  Feeds NPU, display, storage.",
            "ppll":   "PCIe/USB PLL — 1200 MHz.",
            "aupll":  "Audio PLL — 786.432 MHz for accurate audio sample rates.",
            "npll":   "NPU PLL.",
            "v0pll":  "Video PLL 0 — pixel clock source for VOP2 port 0.",
            "v1pll":  "Video PLL 1 — pixel clock source for VOP2 port 1.",
        },
        irq_doc="N/A — the CRU does not generate interrupts.",
        trm_refs=[
            "RK3588 TRM Part 1, Chapter 5 — Clock",
            "Linux kernel: drivers/clk/rockchip/clk-rk3588.c",
        ],
        pitfalls=[
            "Changing a PLL that feeds multiple devices (e.g. GPLL) affects "
            "ALL consumers simultaneously; use per-device dividers instead.",
            "Audio PLLs (aupll) must not be changed after audio starts "
            "or you get sample-rate drift / clicks.",
        ],
    ),
}

# ── Fuzzy key lookup ──────────────────────────────────────────────────────────


def _lookup_doc(compat: str, dev_name: str) -> Optional[HWBlockDoc]:
    """Find the best matching HWBlockDoc for a compatible + device name."""
    combined = (compat + " " + dev_name).lower()
    # Exact key match first
    for key, doc in _KB.items():
        if key in combined:
            return doc
    # Keyword fallback
    _FALLBACK: List[Tuple[str, str]] = [
        ("i2c",      "snps,dw-apb-i2c"),
        ("spi",      "rockchip,rk3588-spi"),
        ("uart",     "snps,dw-apb-uart"),
        ("serial",   "snps,dw-apb-uart"),
        ("gpio",     "rockchip,gpio-bank"),
        ("pcie",     "rockchip,rk3588-pcie3"),
        ("usb",      "rockchip,rk3588-dwc3"),
        ("gmac",     "rockchip,rk3588-gmac"),
        ("ethernet", "rockchip,rk3588-gmac"),
        ("vop",      "rockchip,rk3588-vop"),
        ("display",  "rockchip,rk3588-vop"),
        ("emmc",     "rockchip,rk3588-dw-mshc"),
        ("mmc",      "rockchip,rk3588-dw-mshc"),
        ("mshc",     "rockchip,rk3588-dw-mshc"),
        ("npu",      "rockchip,rk3588-rknn"),
        ("rknn",     "rockchip,rk3588-rknn"),
        ("cru",      "rockchip,rk3588-cru"),
        ("pmic",     "rockchip,rk806"),
        ("rk806",    "rockchip,rk806"),
        ("rk817",    "rockchip,rk817"),
        ("gic",      "arm,gic-v3"),
    ]
    for kw, kb_key in _FALLBACK:
        if kw in combined:
            return _KB.get(kb_key)
    return None


# ── Node extraction helpers ───────────────────────────────────────────────────


def _get_compat(node) -> str:
    c = node.properties.get("compatible", "")
    if isinstance(c, (list, tuple)):
        return ", ".join(str(x) for x in c)
    return str(c)


def _get_reg_summary(node) -> str:
    reg = node.properties.get("reg")
    if isinstance(reg, (list, tuple)) and reg:
        try:
            base = int(reg[0])
            if len(reg) >= 2:
                size = int(reg[1])
                return f"0x{base:x} (size 0x{size:x})"
            return f"0x{base:x}"
        except (TypeError, ValueError):
            pass
    if isinstance(reg, (int, float)):
        return f"0x{int(reg):x}"
    return "(not specified)"


def _get_clock_summary(node, soc: SoC) -> List[str]:
    names = node.properties.get("clock-names", [])
    if isinstance(names, str):
        names = [names]
    return list(names)


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class ExplainResult:
    node_path: str
    device_name: str
    compatible: str
    reg_summary: str
    status: str
    block_name: str
    description: str
    clock_names: List[str]
    clock_explanations: Dict[str, str]
    irq_doc: str
    trm_refs: List[str]
    pitfalls: List[str]
    typical_config: str


# ── Main API ──────────────────────────────────────────────────────────────────


def explain_node(node_path_or_name: str, soc: SoC) -> Optional[ExplainResult]:
    """Look up a node by path or name and return an ExplainResult."""
    target = node_path_or_name.strip()

    # Find the node
    found_name: Optional[str] = None
    found_node = None

    for dev_name, node in soc.devices.items():
        if node.path == target or dev_name == target:
            found_name = dev_name
            found_node = node
            break

    # Fuzzy name match
    if found_node is None:
        target_lower = target.lower().lstrip("/")
        for dev_name, node in soc.devices.items():
            if (target_lower in dev_name.lower()
                    or target_lower in node.path.lower()
                    or target_lower in _get_compat(node).lower()):
                found_name = dev_name
                found_node = node
                break

    if found_node is None:
        return None

    compat = _get_compat(found_node)
    doc = _lookup_doc(compat, found_name)

    if doc is None:
        doc = HWBlockDoc(
            block_name=f"Unknown peripheral ({compat.split(',')[-1].strip()})",
            description=(
                "No documentation entry found in the socc knowledge base. "
                "Check the vendor binding documentation for this compatible string."
            ),
            clock_docs={},
            irq_doc="(unknown)",
            trm_refs=["Search the Linux kernel Documentation/devicetree/bindings/"],
            pitfalls=[],
        )

    clk_names = _get_clock_summary(found_node, soc)
    clk_explanations: Dict[str, str] = {}
    for cn in clk_names:
        expl = doc.clock_docs.get(cn)
        if expl is None:
            # Try partial match
            for key, val in doc.clock_docs.items():
                if key in cn or cn in key:
                    expl = val
                    break
        clk_explanations[cn] = expl or "(clock function not documented here)"

    return ExplainResult(
        node_path=found_node.path,
        device_name=found_name,
        compatible=compat,
        reg_summary=_get_reg_summary(found_node),
        status=str(found_node.properties.get("status", "okay")),
        block_name=doc.block_name,
        description=doc.description,
        clock_names=clk_names,
        clock_explanations=clk_explanations,
        irq_doc=doc.irq_doc,
        trm_refs=doc.trm_refs,
        pitfalls=doc.pitfalls,
        typical_config=getattr(doc, "typical_config", ""),
    )


def list_knowledge_base() -> str:
    """Return a formatted list of all entries in the knowledge base."""
    lines = [
        "socc explain — Knowledge Base",
        f"  {len(_KB)} hardware blocks documented",
        "",
    ]
    for key, doc in sorted(_KB.items()):
        lines.append(f"  {key:45s}  →  {doc.block_name}")
    return "\n".join(lines)


# ── Renderer ──────────────────────────────────────────────────────────────────


def render_explain(result: ExplainResult) -> str:
    wrap = lambda s: textwrap.fill(s, width=70, subsequent_indent="             ")
    lines: List[str] = [
        f"┌{'─'*70}┐",
        f"│ [EXPLAIN] {result.node_path:<58} │",
        f"└{'─'*70}┘",
        "",
        f"  Hardware Block  : {result.block_name}",
        f"  Compatible      : {result.compatible}",
        f"  Register Base   : {result.reg_summary}",
        f"  Status          : {result.status}",
        "",
        "  Description",
        "  ─────────────────────────────────────────────────────────────────",
    ]
    for line in textwrap.wrap(result.description, width=67):
        lines.append(f"  {line}")
    lines.append("")

    if result.clock_names:
        lines.append("  Clocks")
        lines.append("  ─────────────────────────────────────────────────────────────────")
        for cn in result.clock_names:
            expl = result.clock_explanations.get(cn, "")
            lines.append(f"  • {cn}")
            for chunk in textwrap.wrap(expl, width=64):
                lines.append(f"      {chunk}")
        lines.append("")

    lines.append("  Interrupt")
    lines.append("  ─────────────────────────────────────────────────────────────────")
    for chunk in textwrap.wrap(result.irq_doc, width=67):
        lines.append(f"  {chunk}")
    lines.append("")

    if result.pitfalls:
        lines.append("  ⚠  Common Pitfalls")
        lines.append("  ─────────────────────────────────────────────────────────────────")
        for i, p in enumerate(result.pitfalls, 1):
            for j, chunk in enumerate(textwrap.wrap(p, width=65)):
                prefix = f"  {i}. " if j == 0 else "     "
                lines.append(prefix + chunk)
        lines.append("")

    if result.typical_config:
        lines.append("  Typical DTS snippet")
        lines.append("  ─────────────────────────────────────────────────────────────────")
        lines.append(f"      {result.typical_config}")
        lines.append("")

    if result.trm_refs:
        lines.append("  📖  Where to Look")
        lines.append("  ─────────────────────────────────────────────────────────────────")
        for ref in result.trm_refs:
            lines.append(f"  • {ref}")
        lines.append("")

    return "\n".join(lines)
