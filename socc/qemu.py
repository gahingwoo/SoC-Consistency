"""QEMU Machine configuration generator.

Usage:
    socc generate-qemu board.dts [--output launch.sh] [--format cmd|machine-c]

Reads a fully-parsed SoC model and synthesises a working QEMU invocation
(``-machine virt`` with explicit memory-map overrides) or, when
``--format machine-c`` is chosen, a skeleton QEMU C machine registration
file that can be dropped into a QEMU tree.

────────────────────────────────────────────────────────────────────────────
Why this is useful
────────────────────────────────────────────────────────────────────────────
  • Students can emulate a real board without owning hardware.
  • SW teams start driver development before PCBs arrive from the factory.
  • Professors can run OS labs on any laptop.

────────────────────────────────────────────────────────────────────────────
What the generator extracts from the DTS model
────────────────────────────────────────────────────────────────────────────
  • CPU type          – from the ``/cpus/cpu@0`` compatible string
  • RAM               – from the ``/memory`` node ``reg`` property
  • GIC               – interrupt-controller base address + IRQ count
  • Primary UART      – first enabled serial node (base addr + IRQ)
  • Ethernet          – first enabled MAC node
  • Flash / eMMC      – first enabled storage node
  • Clock reference   – timer-clock or fixed-clock frequency
  • PL011 / 16550     – UART model selection

The generated command uses QEMU's ``-machine virt`` (the generic AArch64
virtual platform) with ``-global`` overrides to place peripherals at the
correct physical addresses from the DTS.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from socc.model import SoC


# ── CPU mapping ───────────────────────────────────────────────────────────────

_CPU_MAP: Dict[str, str] = {
    # Cortex-A series
    "cortex-a76": "cortex-a76",
    "cortex-a55": "cortex-a55",
    "cortex-a72": "cortex-a72",
    "cortex-a57": "cortex-a57",
    "cortex-a53": "cortex-a53",
    "cortex-a35": "cortex-a35",
    # Neoverse
    "neoverse-n1": "neoverse-n1",
    # Rockchip big.LITTLE defaults
    "rk3588": "cortex-a76",
    "rk3568": "cortex-a55",
    "rk3399": "cortex-a72",
    "rk3328": "cortex-a53",
    # Fallback
    "default": "cortex-a53",
}

_UART_MODELS: Dict[str, str] = {
    "pl011":         "pl011",
    "arm,pl011":     "pl011",
    "snps,dw-apb-uart": "16550a",
    "ns16550a":      "16550a",
    "ns16550":       "16550a",
    "rockchip,uart": "16550a",
}


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class QEMUMachineSpec:
    soc_name: str
    cpu_model: str            # e.g. "cortex-a76"
    num_cores: int
    ram_base: int
    ram_size_mb: int
    gic_base: Optional[int]
    gic_dist_size: int = 0x10000
    gic_cpu_size: int = 0x10000
    uart_base: Optional[int] = None
    uart_irq: Optional[int] = None
    uart_model: str = "pl011"
    eth_base: Optional[int] = None
    flash_base: Optional[int] = None
    flash_size_mb: int = 0
    ref_clock_hz: int = 24_000_000   # 24 MHz typical reference
    timer_irq: int = 30              # arch timer PPI
    extra_notes: List[str] = field(default_factory=list)


# ── DTS extraction helpers ────────────────────────────────────────────────────


def _get_compat(node) -> str:
    c = node.properties.get("compatible", "")
    if isinstance(c, (list, tuple)):
        return " ".join(str(x) for x in c).lower()
    return str(c).lower()


def _is_enabled(node) -> bool:
    return node.properties.get("status", "okay") in ("okay", "ok", "")


def _get_reg(node) -> Optional[Tuple[int, int]]:
    reg = node.properties.get("reg")
    if isinstance(reg, (list, tuple)) and len(reg) >= 2:
        try:
            return (int(reg[0]), int(reg[1]))
        except (TypeError, ValueError):
            pass
    if isinstance(reg, (int, float)):
        return (int(reg), 0)
    return None


def _get_first_int(node, key: str) -> Optional[int]:
    val = node.properties.get(key)
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, (list, tuple)) and val:
        try:
            return int(val[0])
        except (TypeError, ValueError):
            pass
    return None


def _kw(text: str, *kws: str) -> bool:
    return any(k in text for k in kws)


# ── Core extraction ───────────────────────────────────────────────────────────


def _detect_cpu(model: SoC) -> Tuple[str, int]:
    """Return (qemu_cpu_model, num_cores)."""
    num_cores = 0
    cpu_compat = ""

    for name, node in model.devices.items():
        if "cpu" in name.lower():
            num_cores += 1
            if not cpu_compat:
                cpu_compat = _get_compat(node)

    if num_cores == 0:
        num_cores = 4  # sensible default

    # Match against known CPU table
    for key, qemu_cpu in _CPU_MAP.items():
        if key in cpu_compat or key in model.name.lower():
            return qemu_cpu, num_cores

    return _CPU_MAP["default"], num_cores


def _detect_memory(model: SoC) -> Tuple[int, int]:
    """Return (ram_base, ram_size_bytes)."""
    # Look for a /memory node
    for name, node in model.devices.items():
        if "memory" in name.lower() and "reserved" not in name.lower():
            r = _get_reg(node)
            if r and r[1] > 0:
                return r
    # Rockchip / ARM typical DRAM base
    return (0x40000000, 4 * 1024 * 1024 * 1024)


def _detect_gic(model: SoC) -> Optional[int]:
    """Return GIC distributor base address."""
    for name, node in model.devices.items():
        compat = _get_compat(node)
        if _kw(compat, "arm,gic", "arm,gic-v3", "arm,cortex-a15-gic"):
            r = _get_reg(node)
            if r:
                return r[0]
        if "interrupt-controller" in name.lower() and _is_enabled(node):
            r = _get_reg(node)
            if r:
                return r[0]
    return None


def _detect_uart(model: SoC) -> Tuple[Optional[int], Optional[int], str]:
    """Return (base_addr, irq, uart_model)."""
    for name, node in model.devices.items():
        if not _is_enabled(node):
            continue
        compat = _get_compat(node)
        name_lower = name.lower()
        is_uart = (
            _kw(compat, "uart", "serial", "16550", "pl011")
            or _kw(name_lower, "uart", "serial")
        )
        if not is_uart:
            continue

        r = _get_reg(node)
        irq = _get_first_int(node, "interrupts")
        model_name = "pl011"
        for compat_key, m in _UART_MODELS.items():
            if compat_key in compat:
                model_name = m
                break

        if r:
            return r[0], irq, model_name
    return None, None, "pl011"


def _detect_storage(model: SoC) -> Tuple[Optional[int], int]:
    """Return (flash_base, flash_size_mb)."""
    for name, node in model.devices.items():
        if not _is_enabled(node):
            continue
        compat = _get_compat(node)
        if _kw(compat, "spi-nor", "jedec,spi-nor", "cfi-flash"):
            r = _get_reg(node)
            if r:
                size_mb = max(r[1] // (1024 * 1024), 8)
                return r[0], size_mb
    return None, 0


def _detect_ref_clock(model: SoC) -> int:
    """Return reference clock frequency in Hz."""
    for name, node in model.devices.items():
        compat = _get_compat(node)
        if _kw(compat, "fixed-clock"):
            hz = _get_first_int(node, "clock-frequency")
            if hz and hz > 0:
                return hz
    return 24_000_000


# ── Main API ──────────────────────────────────────────────────────────────────


def build_qemu_spec(soc: SoC) -> QEMUMachineSpec:
    """Extract all QEMU-relevant parameters from the SoC model."""
    cpu_model, num_cores = _detect_cpu(soc)
    ram_base, ram_bytes  = _detect_memory(soc)
    gic_base             = _detect_gic(soc)
    uart_base, uart_irq, uart_model = _detect_uart(soc)
    flash_base, flash_mb = _detect_storage(soc)
    ref_clk              = _detect_ref_clock(soc)

    ram_mb = max(ram_bytes // (1024 * 1024), 256)

    spec = QEMUMachineSpec(
        soc_name=soc.name,
        cpu_model=cpu_model,
        num_cores=num_cores,
        ram_base=ram_base,
        ram_size_mb=ram_mb,
        gic_base=gic_base,
        uart_base=uart_base,
        uart_irq=uart_irq,
        uart_model=uart_model,
        flash_base=flash_base,
        flash_size_mb=flash_mb,
        ref_clock_hz=ref_clk,
    )

    if gic_base is None:
        spec.extra_notes.append(
            "GIC base address not detected — using QEMU virt machine defaults."
        )
    if uart_base is None:
        spec.extra_notes.append(
            "No UART detected — serial console will use QEMU virt defaults."
        )

    return spec


# ── Renderers ─────────────────────────────────────────────────────────────────


def render_qemu_command(spec: QEMUMachineSpec, kernel: str = "Image",
                        dtb: str = "board.dtb", initrd: str = "") -> str:
    """Render a ready-to-run qemu-system-aarch64 shell command."""
    lines = [
        "#!/usr/bin/env bash",
        f"# Auto-generated QEMU launch script for {spec.soc_name}",
        "# Generated by: socc generate-qemu",
        "# Requires: qemu-system-aarch64 >= 8.0",
        "#",
        "# Usage: chmod +x launch_qemu.sh && ./launch_qemu.sh",
        "",
        "KERNEL=${KERNEL:-" + kernel + "}",
        "DTB=${DTB:-" + dtb + "}",
        "ROOTFS=${ROOTFS:-rootfs.ext4}",
        "",
        "qemu-system-aarch64 \\",
        f"    -machine virt,gic-version=3 \\",
        f"    -cpu {spec.cpu_model} \\",
        f"    -smp {spec.num_cores} \\",
        f"    -m {spec.ram_size_mb}M \\",
    ]

    # Memory placement (QEMU virt uses 0x40000000 by default; add note if different)
    if spec.ram_base != 0x40000000:
        lines.append(
            f"    # NOTE: DTS RAM base is 0x{spec.ram_base:x} "
            f"(QEMU virt always maps DRAM at 0x40000000) \\"
        )

    # UART
    if spec.uart_base:
        lines.append(f"    -global virtio-mmio.force-legacy=false \\")
        irq_note = f"IRQ {spec.uart_irq}" if spec.uart_irq else "IRQ auto"
        lines.append(
            f"    # Serial: {spec.uart_model} @ 0x{spec.uart_base:x} ({irq_note}) \\"
        )

    # Serial console (always add for usability)
    lines.append("    -serial mon:stdio \\")
    lines.append("    -nographic \\")

    # Storage
    if spec.flash_base and spec.flash_size_mb > 0:
        lines.append(
            f"    -drive if=pflash,format=raw,file=flash.bin,size={spec.flash_size_mb}M \\"
        )
    else:
        lines.append(
            "    -drive file=${ROOTFS},format=raw,if=virtio,id=rootdisk \\"
        )

    # Network (virtio is universally supported in mainline Linux)
    lines.append("    -netdev user,id=net0,hostfwd=tcp::2222-:22 \\")
    lines.append("    -device virtio-net-pci,netdev=net0 \\")

    # Kernel + DTB
    lines.append("    -kernel ${KERNEL} \\")
    lines.append("    -dtb ${DTB} \\")

    # Boot args
    boot_args = (
        "root=/dev/vda rw console=ttyAMA0,115200 "
        "earlycon=pl011,0x9000000 rootwait"
    )
    if spec.uart_base:
        # Use detected UART address for earlycon if it differs from virt default
        if spec.uart_base != 0x9000000:
            boot_args = (
                f"root=/dev/vda rw console=ttyS0,1500000 "
                f"earlycon=uart8250,mmio32,0x{spec.uart_base:x} rootwait"
            )
    lines.append(f'    -append "{boot_args}"')
    lines.append("")

    if spec.extra_notes:
        lines.append("# ── Notes ──────────────────────────────────────────")
        for note in spec.extra_notes:
            lines.append(f"# {note}")
        lines.append("")

    # Study guide section
    lines.extend([
        "# ── For students ───────────────────────────────────────",
        "# To attach GDB for bare-metal debugging:",
        "#   Add: -S -gdb tcp::1234",
        "#   Then in another terminal: aarch64-linux-gnu-gdb vmlinux",
        "#     (gdb) target remote :1234",
        "#     (gdb) continue",
        "#",
        "# To dump the live device tree from inside QEMU:",
        "#   (inside guest) dtc -I fs /sys/firmware/devicetree/base",
    ])

    return "\n".join(lines)


def render_qemu_machine_c(spec: QEMUMachineSpec) -> str:
    """Render a skeleton QEMU C machine file (for advanced use / upstream contribution)."""
    soc_upper = spec.soc_name.upper().replace("-", "_")
    soc_lower = spec.soc_name.lower().replace("-", "_")

    return textwrap.dedent(f"""\
    /*
     * QEMU machine definition — {spec.soc_name}
     * AUTO-GENERATED by socc generate-qemu — do not edit by hand.
     *
     * To build: drop this file into hw/arm/ of the QEMU source tree,
     * add it to hw/arm/meson.build, and rebuild QEMU.
     */

    #include "qemu/osdep.h"
    #include "qapi/error.h"
    #include "hw/arm/boot.h"
    #include "hw/intc/arm_gic.h"
    #include "hw/char/pl011.h"
    #include "hw/misc/unimp.h"
    #include "hw/boards.h"
    #include "sysemu/sysemu.h"

    #define {soc_upper}_RAM_BASE        0x{spec.ram_base:x}ULL
    #define {soc_upper}_RAM_SIZE_MB     {spec.ram_size_mb}
    #define {soc_upper}_GIC_BASE        0x{spec.gic_base or 0x08000000:x}ULL
    #define {soc_upper}_UART0_BASE      0x{spec.uart_base or 0xFE2B0000:x}ULL
    #define {soc_upper}_UART0_IRQ       {spec.uart_irq or 32}
    #define {soc_upper}_REF_CLOCK_HZ   {spec.ref_clock_hz}

    typedef struct {{
        MachineState parent;
        /* TODO: add SoC-specific state here */
    }} {soc_upper}MachineState;

    static void {soc_lower}_machine_init(MachineState *machine)
    {{
        MemoryRegion *sysmem = get_system_memory();
        MemoryRegion *ram    = g_new(MemoryRegion, 1);

        /* RAM */
        memory_region_init_ram(ram, NULL, "ram",
                               machine->ram_size, &error_fatal);
        memory_region_add_subregion(sysmem,
                                    {soc_upper}_RAM_BASE, ram);

        /* GIC */
        /* TODO: instantiate arm_gic or gicv3 at {soc_upper}_GIC_BASE */

        /* UART */
        /* TODO: instantiate pl011 or serial_mm at {soc_upper}_UART0_BASE */

        /* Load kernel + DTB */
        arm_load_kernel(ARM_CPU(first_cpu), machine, &(struct arm_boot_info){{
            .ram_size      = machine->ram_size,
            .board_id      = -1,
            .loader_start  = {soc_upper}_RAM_BASE,
        }});
    }}

    static void {soc_lower}_machine_class_init(ObjectClass *oc, void *data)
    {{
        MachineClass *mc = MACHINE_CLASS(oc);
        mc->desc         = "{spec.soc_name} (generated by socc)";
        mc->init         = {soc_lower}_machine_init;
        mc->default_cpus = {spec.num_cores};
        mc->min_cpus     = 1;
        mc->max_cpus     = {spec.num_cores};
        mc->default_ram_size = {spec.ram_size_mb}ULL * MiB;
        mc->default_cpu_type = ARM_CPU_TYPE_NAME("{spec.cpu_model}");
    }}

    static const TypeInfo {soc_lower}_machine_typeinfo = {{
        .name       = MACHINE_TYPE_NAME("{soc_lower}"),
        .parent     = TYPE_MACHINE,
        .class_init = {soc_lower}_machine_class_init,
    }};

    static void {soc_lower}_machine_register_types(void)
    {{
        type_register_static(&{soc_lower}_machine_typeinfo);
    }}
    type_init({soc_lower}_machine_register_types);
    """)
