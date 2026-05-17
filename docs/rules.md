# Rules Reference

socc ships two categories of rules:

- **Registry rules** — loaded through the rule registry and run by `socc check`.
  These perform cross-domain constraint checking against the full SoC model.
- **Diagnostic module rules** — run by dedicated subcommands (`check-memory`,
  `check-deps`, `check-bounds`, `check-irq`).  Each module uses a focused
  algorithm (sweep-line, DFS, etc.) rather than the generic rule framework.

Run `socc rules` to list all active registry rules with their current
descriptions.

---

## Quick Reference — Registry Rules

| Code       | Sev | Subsystem   | Description                                               |
|------------|-----|-------------|-----------------------------------------------------------|
| PD-001     | E   | Power       | `pd-supply` references a domain that does not exist       |
| PD-002     | E   | Power       | Regulator not declared in SoC constraint file             |
| PD-003     | E   | Power       | Circular dependency in regulator supply chain             |
| PD-004     | W   | Power       | Regulator output voltage outside allowed range            |
| PD-005     | W   | Power       | Load imbalance on a single regulator                      |
| PD-006     | I   | Power       | Regulator defined but not consumed by any device          |
| PD-007     | E   | Power       | IO supply appears after core supply in startup sequence   |
| PD-008     | W   | Power       | PMIC channel current over-committed                       |
| CK-101     | E   | Clock       | Cycle detected in clock tree                              |
| CK-102     | W   | Clock       | Clock consumer references unknown provider                |
| CK-103     | W   | Clock       | Clock frequency outside SoC specification                 |
| CK-104     | I   | Clock       | Clock provider has no consumers                           |
| CK-105     | E   | Clock       | Clock divider value is invalid                            |
| CK-106     | W   | Clock       | Multiple consumers contend on the same clock source       |
| PIN-201    | E   | GPIO        | GPIO pin claimed by more than one device                  |
| PIN-202    | E   | GPIO        | GPIO voltage domain mismatch                              |
| PIN-301    | E   | GPIO        | Bus controller enabled without `pinctrl-0` binding        |
| MEM-301    | E   | Memory      | Memory region address or size overlap                     |
| MEM-302    | W   | Memory      | Memory timing constraint violated                         |
| MEM-303    | W   | Memory      | Memory capacity allocation exceeds physical RAM           |
| BUS-401    | E   | Bus         | I2C/SPI slave address collision on same bus               |
| BUS-402    | W   | Bus         | Bus clock frequency mismatch between nodes                |
| BUS-403    | W   | Bus         | Slave response timeout too high                           |
| IRQ-501    | E   | Interrupt   | Interrupt line assigned to more than one device           |
| IRQ-502    | W   | Interrupt   | Interrupt priority inverted for critical device           |
| THM-001    | E   | Thermal     | Critical trip temperature at or above Tj_MAX              |
| THM-002    | E   | Thermal     | Thermal zone has no `critical` trip point                 |
| THM-003    | W   | Thermal     | Passive zone has `polling-delay-passive = 0`              |
| BW-101     | W   | Bandwidth   | Sum of high-BW peripheral budgets saturates DDR           |
| BW-102     | I   | Bandwidth   | High-BW peripheral enabled without QoS configuration      |
| SEC-201    | E   | Security    | DMA master may access secure memory carveout              |
| SEC-202    | W   | Security    | Cryptographic peripheral accessible from Normal World     |
| SEC-203    | I   | Security    | Debug/trace interface enabled in production DTS           |
| NET-601    | E   | Netlist     | DTS pinmux assignment differs from EDA netlist            |
| NET-602    | W   | Netlist     | Netlist pin has no matching DTS pinctrl entry             |
| COMP-101   | W   | Compat      | Deprecated vendor BSP compatible string                   |
| GEN-401    | I   | General     | Node defined but never referenced                         |
| PWR-101    | W   | Power mgmt  | `regulator-always-on` on non-critical peripheral rail     |
| PWR-102    | W   | Power mgmt  | PMIC control node missing `wakeup-source`                 |
| AW-001     | E   | Allwinner   | AXP PMIC supply missing                                   |
| AW-002     | W   | Allwinner   | AXP PMIC regulator voltage out of range                   |
| AW-003     | E   | Allwinner   | Circular dependency in Allwinner power tree               |
| AW-101     | E   | Allwinner   | CCU clock provider missing                                |
| AW-102     | W   | Allwinner   | Invalid CCU clock frequency                               |
| AW-103     | W   | Allwinner   | R_CCU required for always-on domain                       |
| AW-201     | E   | Allwinner   | `pio` pinctrl node missing                                |
| AW-202     | E   | Allwinner   | 1.8V GPIO bank driven from 3.3V supply                    |
| AW-203     | W   | Allwinner   | Excessive GPIO pin assignment for peripheral              |
| ML-001     | E   | Amlogic     | Device in AO domain uses EE supply (or vice versa)        |
| ML-002     | E   | Amlogic     | `vddao` supply missing                                    |
| ML-003     | E   | Amlogic     | Circular dependency in Amlogic power tree                 |
| ML-101     | E   | Amlogic     | Peripheral missing CLKC / AO CLKC clock reference         |
| ML-102     | W   | Amlogic     | Clock frequency out of CLKC range                         |
| ML-103     | E   | Amlogic     | AO domain device references main CLKC                     |
| ML-201     | E   | Amlogic     | `periphs-pinctrl` or `aobus-pinctrl` node missing         |
| ML-202     | E   | Amlogic     | 1.8V GPIO bank (GPIOC/GPIOA) driven from 3.3V supply      |
| ML-203     | E   | Amlogic     | GPIOAO pin configured via `periphs-pinctrl`               |
| QC-001     | E   | Qualcomm    | RPMh power domain provider missing                        |
| QC-002     | W   | Qualcomm    | SPMI PMIC bus node missing                                |
| QC-003     | E   | Qualcomm    | CX power rail missing                                     |
| QC-101     | E   | Qualcomm    | GCC clock controller missing                              |
| QC-102     | W   | Qualcomm    | Clock frequency outside OPP bounds                        |
| QC-103     | W   | Qualcomm    | DSP subsystem firmware node missing                       |
| QC-201     | E   | Qualcomm    | TLMM GPIO controller missing                              |
| QC-202     | E   | Qualcomm    | TLMM pad connected to 3.3V without level-shifter          |
| QC-203     | I   | Qualcomm    | Debug UART node missing                                   |
| IMX-001    | E   | NXP i.MX   | ARM PLL frequency exceeds 1800 MHz on i.MX8M Plus         |
| IMX-002    | E   | NXP i.MX   | DRAM supply enabled before SoC core supply                |

**Severity key:** E = error, W = warning, I = info

---

## Quick Reference — Diagnostic Module Rules

These rules are reported by the dedicated diagnostic subcommands.

### `socc check-memory`

| Code   | Sev | Description                                        |
|--------|-----|----------------------------------------------------|
| MM-001 | E   | Two nodes map identical MMIO windows               |
| MM-002 | E   | One MMIO region fully contains another             |
| MM-003 | E   | Two MMIO regions partially overlap                 |
| MM-004 | W   | `reg` entry has zero size                          |
| MM-005 | W   | Single mapping larger than 512 MiB                 |

### `socc check-deps`

| Code    | Sev | Description                                                |
|---------|-----|------------------------------------------------------------|
| DG-CP01 | E   | Cycle detected in power supply dependency graph            |
| DG-CK01 | E   | Cycle detected in clock parent chain                       |
| DG-OP01 | E   | Regulator references a parent rail that is not declared    |
| DG-OK01 | E   | Clock references a parent that is not declared             |
| DG-FP01 | W   | Single power rail drives an unusually high number of loads |
| DG-FK01 | W   | Single clock provider has an unusually high fan-out        |

### `socc check-bounds`

| Code    | Sev | Description                                            |
|---------|-----|--------------------------------------------------------|
| BND-001 | E   | GPIO pin index exceeds physical bank size              |
| BND-002 | E   | DMA channel number exceeds controller capacity         |
| BND-003 | E   | PWM channel index exceeds controller capacity          |

### `socc check-irq`

| Code    | Sev      | Description                                                     |
|---------|----------|-----------------------------------------------------------------|
| IRQ-C01 | critical | Two active nodes share the same non-shared GIC SPI/PPI line     |
| IRQ-C02 | E        | Device bound to architecturally reserved PPI (SGI/FIQ/timer)   |
| IRQ-C03 | E        | `interrupt-parent` points to a missing or disabled controller   |
| IRQ-C04 | W        | Interrupt controller missing `#interrupt-cells` property        |

---

## Severity Levels

| Level    | Meaning                                                               |
|----------|-----------------------------------------------------------------------|
| critical | Hardware-level conflict. System will hang or corrupt state at runtime.|
| error    | Device or subsystem will not function. Must be fixed before shipping. |
| warning  | May cause instability, performance degradation, or silent failure.    |
| info     | Informational. No immediate action required but worth reviewing.      |

---

## Detailed Reference

### Power Domain (PD-xxx)

#### PD-001 — Power domain not found

**Severity**: error | **Applies to**: all SoCs

A device node's supply property references a power domain label that does not
exist anywhere in the device tree.

**Fix**: ensure the referenced power domain node is present and correctly
labelled, or correct the phandle reference.

---

#### PD-002 — Regulator not defined in SoC constraints

**Severity**: error | **Applies to**: Rockchip (rk3588, rk3568, rk3399)

A regulator node is present in the DTS but the SoC constraint file
(`data/soc/<vendor>/<soc>.yaml`) does not list it.

**Fix**: add the regulator to the YAML constraint file, or remove the node.

---

#### PD-003 — Regulator circular dependency

**Severity**: error | **Applies to**: all SoCs

The regulator supply chain contains a cycle (A supplies B which supplies A).
The kernel regulator framework cannot resolve boot order and will deadlock.

**Fix**: break the cycle; ensure the supply graph is a directed acyclic graph.

---

#### PD-007 — IO supply before core supply

**Severity**: error | **Applies to**: all SoCs

An IO-level supply (1.5–3.6 V) is the direct parent of a core supply
(≤1.25 V), but the sequencing annotation is missing or inverted.  The core
supply may attempt to enable before its parent IO rail is stable.

**Fix**: add `startup-delay-us` or reorder the supply chain to match the
datasheet power-on sequence.

---

#### PD-008 — PMIC channel over-committed

**Severity**: warning | **Applies to**: SoCs with PMIC YAML constraints

Total theoretical load on a PMIC output channel exceeds `max_current_ma`
from the YAML constraint file.

**Fix**: balance loads across PMIC channels, or select a PMIC with a higher
current rating on the affected channel.

---

### Clock Tree (CK-xxx)

#### CK-101 — Clock tree cycle

**Severity**: error | **Applies to**: all SoCs

A clock provider is listed as its own ancestor.  The clock framework cannot
initialise such a tree.

---

#### CK-105 — Invalid clock divider

**Severity**: error | **Applies to**: all SoCs

A `clock-div` or `assigned-clock-rates` value implies a divider of zero,
negative, or a non-power-of-two where the hardware requires it.

---

### GPIO / Pin-Mux (PIN-xxx)

#### PIN-201 — Duplicate pin definition

**Severity**: error | **Applies to**: all SoCs

Two device nodes reference the same physical pad in their `pinctrl-0` group.
The last binding silently wins; the first device's signal is unrouted.

---

#### PIN-202 — Voltage domain mismatch

**Severity**: error | **Applies to**: all SoCs

The IO supply voltage for a pin group is incompatible with the signal voltage
of the connected peripheral.

---

#### PIN-301 — Phantom peripheral

**Severity**: error | **Applies to**: all SoCs

A bus controller (I2C, SPI, UART, PWM, CAN, SD/MMC) is enabled
(`status = "okay"`) but has no `pinctrl-0` binding.  The driver probes
and creates the `/dev` entry, but the pads remain in GPIO state — the
peripheral is electrically disconnected.

---

### Memory Map (MEM-xxx)

#### MEM-301 — Addressing error

**Severity**: error | **Applies to**: all SoCs

Two `memory` nodes declare overlapping or identical address ranges.  One
driver will corrupt the other's register space at `ioremap()` time.

---

### Interrupt (IRQ-xxx)

#### IRQ-501 — Duplicate interrupt

**Severity**: error | **Applies to**: all SoCs

Two device nodes share the same interrupt number.  The kernel returns
`-EBUSY` on the second registration, or both handlers fire on every
interrupt.

---

### Thermal (THM-xxx)

#### THM-002 — Missing critical trip point

**Severity**: error | **Applies to**: all SoCs

A thermal zone defines trip points but none is of type `critical`.  Without
a critical trip, there is no last-resort hardware shutdown path; the SoC
will continue drawing power past its safe junction temperature.

---

### Security (SEC-xxx)

#### SEC-201 — DMA access to secure carveout

**Severity**: error | **Applies to**: all ARM SoCs with TrustZone

A DMA-capable peripheral has potential access to a `no-map` memory region
used for OP-TEE or TF-A carveouts.  Exploitation can leak TrustZone secrets
or allow privilege escalation.

**Fix**: add IOMMU/SMMU mapping restrictions, or move the carveout outside
the peripheral's DMA window.

---

### Netlist Cross-Check (NET-xxx)

Requires `--netlist <csv>` on `socc check`.

#### NET-601 — Pinmux netlist mismatch

**Severity**: error

A pin's function declared in the DTS differs from the net name in the EDA
schematic CSV export.

#### NET-602 — Unclaimed netlist pin

**Severity**: warning

A pin present in the EDA netlist has no matching `pinctrl` entry in the DTS.
The pad may be left floating or in an unintended default state.

---

### Compatibility (COMP-xxx)

#### COMP-101 — Deprecated vendor BSP compatible string

**Severity**: warning | **Applies to**: all SoCs

A `compatible` string exists only in a vendor BSP kernel or was renamed
during mainline upstreaming.  The device will not be recognised by a mainline
kernel.

**Fix**: update to the mainline-upstream compatible string, or add it as a
second entry alongside the vendor string.

---

### Allwinner (AW-xxx)

#### AW-202 — 1.8V GPIO bank driven at 3.3V

**Severity**: error | **Applies to**: H616, H618, H3, A64

Certain Allwinner GPIO banks (PC on H616, PG on H3/A64) operate at 1.8V.
Supplying them from a 3.3V rail permanently damages the IO pads.

**Fix**: ensure the bank's IO supply comes from a 1.8V LDO.

---

### Amlogic (ML-xxx)

#### ML-001 — AO/EE domain mismatch

**Severity**: error | **Applies to**: all Amlogic SoCs

Amlogic SoCs separate the Always-On (AO) and External-Entity (EE) power
planes.  A mismatch causes the affected device to lose power on every suspend
cycle, resulting in a resume panic.

#### ML-103 — AO domain clock crossing

**Severity**: error | **Applies to**: all Amlogic SoCs

Devices in the AO domain must reference `ao_clkc`.  Referencing the main
`clkc` causes those devices to lose their clock on suspend, making resume
impossible.

---

### Qualcomm (QC-xxx)

#### QC-101 — GCC missing

**Severity**: error | **Applies to**: SDM845, SM8250, SM8550, SC7180, QCS6490

The Global Clock Controller (GCC) is the primary clock provider on Qualcomm
SoCs.  Without the `gcc` node, every peripheral clock consumer fails to probe.

#### QC-202 — TLMM voltage conflict

**Severity**: error | **Applies to**: all Qualcomm SoCs

Qualcomm TLMM pads default to 1.8V IO.  Connecting 3.3V signals without
a level-shifter or a configurable LDO violates the datasheet and can
permanently damage the SoC.

---

### NXP i.MX (IMX-xxx)

#### IMX-002 — DRAM power sequencing

**Severity**: error | **Applies to**: i.MX8M Plus

Per NXP AN13486, the DRAM supply (NVCC_DRAM / BUCK3) must reach its target
voltage before the SoC core supply (VDD_SOC / BUCK1) is enabled.  Inverting
this sequence causes DRAM PHY calibration failure and a non-bootable board.

---

## Diagnostic Module Rules — Detailed Reference

### MM-xxx — MMIO Overlap Scanner (`socc check-memory`)

The scanner builds a sorted list of all `reg` windows from enabled nodes and
runs a sweep-line algorithm to detect every class of address-space collision.

| Code   | Description                                                                  |
|--------|------------------------------------------------------------------------------|
| MM-001 | Two nodes declare exactly the same base address and size (duplicate node).   |
| MM-002 | One region's window entirely contains another (one driver shadows the other).|
| MM-003 | Two regions share some but not all addresses (partial overlap).              |
| MM-004 | A `reg` entry has size 0; `ioremap()` will return NULL.                      |
| MM-005 | A single mapping exceeds 512 MiB (likely a copy-paste size error).           |

---

### DG-xxx — Dependency Graph Analyzer (`socc check-deps`)

Builds directed graphs of the power tree and clock tree, then runs DFS cycle
detection and orphan-node checks.

| Code    | Description                                                              |
|---------|--------------------------------------------------------------------------|
| DG-CP01 | Directed cycle in the power supply graph. Kernel will deadlock at boot.  |
| DG-CK01 | Directed cycle in the clock parent chain. Clock framework cannot init.   |
| DG-OP01 | Regulator names a `vin-supply` parent that is never declared.            |
| DG-OK01 | Clock names a `clocks` parent that is never declared.                    |
| DG-FP01 | A single regulator drives more than the configured fan-out limit.        |
| DG-FK01 | A single clock provider has more consumers than the configured limit.    |

---

### BND-xxx — Physical Bounds Auditor (`socc check-bounds`)

Checks GPIO pin indices, DMA channel numbers, and PWM channel indices against
the hardware limits stored in the SoC database.

| Code    | Description                                                              |
|---------|--------------------------------------------------------------------------|
| BND-001 | `gpios` pin index exceeds the physical bank size (e.g., pin 35 on a     |
|         | 32-pin bank). The driver will panic at probe or silently mis-address.    |
| BND-002 | DMA channel number exceeds the controller's physical channel count.      |
| BND-003 | PWM channel index exceeds the controller's physical output count.        |

**Supported SoC hardware limits:**

| SoC      | GPIO pins / bank | DMA channels | PWM channels |
|----------|-----------------|--------------|--------------|
| RK3588   | 32              | 32           | 16           |
| RK3576   | 32              | 32           | 16           |
| RK3568   | 32              | 32           | 16           |
| RK3399   | 32              | 32           | 8            |

---

### IRQ-Cxx — IRQ Collision Checker (`socc check-irq`)

Builds a map of all interrupt assignments from enabled nodes and checks for
conflicts and routing errors.

| Code    | Description                                                                   |
|---------|-------------------------------------------------------------------------------|
| IRQ-C01 | Two active nodes claim the same non-shared GIC SPI or PPI line simultaneously.|
|         | Results in `-EBUSY` or an interrupt storm at runtime.                         |
| IRQ-C02 | A driver is bound to a PPI in the reserved range (SGI 0–7, PPI 13–15).       |
|         | These lines are architecturally reserved for SGIs, FIQs, and timer IRQs.     |
| IRQ-C03 | `interrupt-parent` points to a node that is missing or has `status = "disabled"`.|
| IRQ-C04 | An interrupt controller node is missing the `#interrupt-cells` property.     |
