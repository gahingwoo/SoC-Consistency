# socc — SoC Device-Tree Consistency Checker

[![PyPI](https://img.shields.io/pypi/v/soc-consistency)](https://pypi.org/project/soc-consistency/)
[![Python](https://img.shields.io/pypi/pyversions/soc-consistency)](https://pypi.org/project/soc-consistency/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**socc** is a static-analysis tool that catches hardware-level bugs in Linux
Device Tree Source (DTS) files — the kind that pass `dtc` and `dt-schema`
validation but still destroy your board at runtime.

If you have ever spent a week chasing a suspend-resume panic caused by a
wrong power-supply phandle, or a silent DMA hang caused by a copy-pasted
channel index that exceeded the controller's physical limit, socc is for you.

```text
$ socc check board.dts --soc rk3588
[FATAL]  PD-001  Power domain crossing — /soc/i2c@fe2b0000 uses vcc_3v3 (EE domain)
                  but is connected to vcc_1v8 (AO domain). System will panic on suspend.
[ERROR]  CK-003  Clock rate mismatch — spi0 requests 50 MHz from pll_cpll (max 24 MHz).
[WARN]   GP-002  GPIO conflict — gpio1_b3 assigned to both spi0 and uart2.
```

---

## Table of Contents

- [Why socc?](#why-socc)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
  - [Core analysis](#core-analysis)
  - [Precision diagnostics](#precision-diagnostics)
  - [Hardware comparison](#hardware-comparison)
  - [Design assistance](#design-assistance)
  - [Vendor BSP tools](#vendor-bsp-tools)
- [Supported SoCs](#supported-socs)
- [CI / CD integration](#ci--cd-integration)
- [Rules reference](#rules-reference)
- [Architecture](#architecture)
- [Contributing](#contributing)

---

## Why socc?

| Tool | What it checks |
|------|---------------|
| `dtc` | DTS syntax |
| `dt-schema` / `dtbs_check` | Property types and required fields per binding |
| **socc** | Whether the *system* is physically and electrically correct |

socc operates on the semantic level: it builds an in-memory model of your
power tree, clock tree, pin-mux table, and interrupt routing, then runs
cross-domain constraint rules that no schema can express.

**Real bugs socc catches:**

| Bug class | Example | Runtime effect |
|-----------|---------|---------------|
| Power domain crossing | AO peripheral on EE supply | Suspend-resume panic |
| Clock rate mismatch | SPI clock > PLL maximum | Bus hangs, data corruption |
| GPIO pin conflict | Two nodes mapped to same pad | Silent last-write-wins |
| MMIO region overlap | Two drivers ioremap same address | Kernel memory corruption |
| GPIO index out of bounds | `gpio1 pin 35` on a 32-pin bank | Kernel panic at driver probe |
| IRQ collision | Two devices share GIC SPI 45 | -EBUSY or interrupt storm |
| DMA channel out of bounds | Channel 48 on 32-channel DMA | Silent DMA failure |
| Power/clock cycle | Regulator A requires B requires A | Kernel deadlock at boot |
| Zombie DTS nodes | 14 disabled camera nodes in BSP | Bloated DTB, slow boot |
| Allwinner 3.3V on 1.8V IO | Wrong PMIC rail to PC bank | Permanent IO pad damage |

---

## Installation

```bash
pip install soc-consistency
```

Or install from source with development extras:

```bash
git clone https://github.com/gahingwoo/SoC-Consistency.git
cd SoC-Consistency
pip install -e ".[dev]"
```

**Requirements:** Python 3.8+, PyYAML, Click 8.0+

---

## Quick start

```bash
# Run all rules against a DTS file
socc check board.dts --soc rk3588

# Auto-fix safe violations and write a patch
socc fix board.dts --soc rk3588 -o board.patch

# Semantic diff between two DTS / DTB files (ignores labels and phandles)
socc smart-diff vendor.dts mainline.dts

# Find zombie nodes (disabled + unreferenced) in a vendor BSP
socc gc rockchip-rk3588-evb.dts

# Detect MMIO region overlaps
socc check-memory board.dts

# Detect copy-paste GPIO/DMA/PWM out-of-bounds errors
socc check-bounds board.dts --soc rk3588

# Detect IRQ collisions and reserved-PPI misuse
socc check-irq board.dts

# Detect power/clock dependency cycles
socc check-deps board.dts

# Interactive shell with SoC model loaded
socc shell board.dts --soc rk3588
```

---

## Commands

### Core analysis

| Command | Description |
|---------|-------------|
| `socc check` | Run all registered rules; output violations by severity |
| `socc fix` | Auto-generate a DTS patch for safe-to-fix violations |
| `socc rules` | List all enabled rules with descriptions |
| `socc shell` | Drop into an interactive Python shell with the SoC model |

#### `socc check`

```bash
socc check board.dts --soc rk3588 \
     --min-severity warning \
     --format json \
     -o report.json
```

Options:
- `--soc` — Target SoC name for hardware-specific rules (e.g. `rk3588`, `rk3568`, `sun50i-h616`)
- `--min-severity` — Filter threshold: `info` | `warning` | `error` | `fatal`
- `--format` — Output format: `text` (default) | `json` | `html`
- `--netlist` — Cross-check against a KiCad or CSV netlist file
- `--enable` / `--disable` — Enable or disable specific rule codes
- `-o` — Write output to file instead of stdout

---

### Precision diagnostics

These commands address the most common hardware bugs caused by vendor BSPs
and copy-paste errors.

#### `socc gc` — DTS Zombie-Node Garbage Collector

Vendor BSPs ship Device Trees that support dozens of board variants.  Your
production board probably activates only a fraction of those nodes.  The rest
are disabled dead-code that bloats the compiled DTB and slows kernel parsing.

```bash
socc gc rockchip-rk3588-evb.dts
```

```text
──────────────────────────────────────────────────────────
SOCC DTS ZOMBIE-NODE GARBAGE COLLECTOR
──────────────────────────────────────────────────────────
  Alive nodes   : 47
  Zombie nodes  : 14
  Est. DTB savings: 8732 bytes (~8.5 KiB)

[CLEANUP] Found 14 unreferenced zombie nodes!
  • /soc/i2c@fe2c0000/camera-sensor@36
    Status: disabled  refs: 0  ~128 bytes
  • /soc/pcie@fe160000
    Status: disabled  refs: 0  ~96 bytes
  ...
  Run: socc gc --apply board.dts  to strip them from the source.
```

#### `socc check-bounds` — Physical Hardware Bounds Auditor

Catches the classic copy-paste bug: changing a GPIO pin index to a value
that exceeds the physical bank size, which compiles fine but panics at probe.

```bash
socc check-bounds board.dts --soc rk3588
```

```text
[FATAL] BND-001  /leds/user-led1
    Property  : gpios = ['&gpio4', 35, 0]
    Pin index 35 is out of range for gpio4 (valid: 0-31)
    Hint: gpio4 only has 32 pins (indices 0-31). Check schematic.
```

Checks GPIO pin indices (`BND-001`), DMA channel numbers (`BND-002`),
and PWM channel indices (`BND-003`) against the SoC hardware database.

#### `socc check-irq` — IRQ Collision & Routing Checker

Detects the hardest-to-debug class of runtime failures: two active devices
sharing the same GIC SPI interrupt line, or a device driver bound to an
architecturally reserved PPI.

```bash
socc check-irq board.dts
```

```text
[CRITICAL] IRQ-C01  GIC SPI IRQ 45
    Non-shared interrupt GIC SPI IRQ 45 claimed by 2 active nodes simultaneously.
    • spi0   -> /soc/spi@fe610000
    • uart0  -> /soc/uart@fe660000
    Hint: Assign unique interrupt lines to each peripheral, or enable IRQF_SHARED.
```

| Rule | Description |
|------|-------------|
| `IRQ-C01` | Two active nodes share the same non-shared interrupt line |
| `IRQ-C02` | Device driver bound to architecturally reserved PPI (SGI/FIQ/timer) |
| `IRQ-C03` | `interrupt-parent` points to missing or disabled controller |
| `IRQ-C04` | Interrupt controller missing `#interrupt-cells` property |

#### `socc check-memory` — MMIO Overlap Scanner

Sweep-line O(n log n) algorithm detects every class of `reg` address-space
collision that causes silent memory corruption at `ioremap()` time.

```bash
socc check-memory board.dts
```

| Rule | Description |
|------|-------------|
| `MM-001` | Identical window — duplicate node |
| `MM-002` | Full containment — one region inside another |
| `MM-003` | Partial overlap — ioremap will corrupt both drivers |
| `MM-004` | Zero-size region — driver cannot map registers |
| `MM-005` | Suspiciously large region (> 512 MiB) |

#### `socc check-deps` — Power/Clock Dependency Analyzer

Builds directed graphs of the power tree and clock tree, then runs DFS cycle
detection and orphan-supply checks.

```bash
socc check-deps board.dts --fan-out-limit 8
```

| Rule | Description |
|------|-------------|
| `DG-CP01` | Cycle in power dependency graph — kernel will deadlock at boot |
| `DG-CK01` | Cycle in clock dependency graph |
| `DG-OP01` | Regulator references a parent rail that does not exist |
| `DG-OK01` | Clock references a parent that does not exist |
| `DG-FP01` | Single power rail drives an unusually high number of consumers |

---

### Hardware comparison

#### `socc smart-diff` — Semantic DTS/DTB Diff

Compares two DTS or DTB files at the hardware-content level, ignoring node
labels, phandle values, comments, and node ordering.  Produces human-readable
annotations for common property semantics.

```bash
socc smart-diff vendor.dtb mainline.dts --format markdown -o diff.md
```

```text
CHANGED  /soc/pcie@fe150000 : max-link-speed  2 → 3  [PCIe Gen2 → Gen3]
CHANGED  /soc/i2c@fe2b0000  : clock-frequency  400000 → 100000  [400 kHz → 100 kHz]
ADDED    /soc/extra@ff000000
REMOVED  /soc/gpu@fe280000
```

Formats: `text` (default), `markdown`, `json`.

#### `socc cross-check` — Bootloader vs. Kernel DTS Comparison

Detects configuration skew between the bootloader DTS and the kernel DTS —
the root cause of "works in U-Boot, hangs in Linux" failures.

```bash
socc cross-check uboot.dts kernel.dts --soc rk3588
```

---

### Design assistance

| Command | Description |
|---------|-------------|
| `socc power-seq` | Visualize the power-on / power-off sequencing order |
| `socc pinmap` | Show the complete pin-mux assignment table |
| `socc topology` | Render a text-art system topology diagram |
| `socc audit` | Multi-domain audit: power, clock, GPIO, IRQ, thermal in one pass |
| `socc simulate` | Simulate power-rail transitions and detect unsafe sequences |
| `socc explain` | Explain a rule violation in plain English with fix examples |

---

### Vendor BSP tools

| Command | Description |
|---------|-------------|
| `socc audit-bom` | Cross-check DTS against a Bill-of-Materials CSV |
| `socc generate-tests` | Generate pytest stubs for the current DTS constraints |
| `socc amp-audit` | Asymmetric Multi-Processing (AMP) resource conflict check |
| `socc trace` | Trace a device's full dependency chain (power + clock + IRQ) |
| `socc migrate` | Assist in porting a DTS from one SoC to another |
| `socc overlay-check` | Validate a DT overlay against its base DTS |

---

## Supported SoCs

socc ships vendor-specific rule packs that know the hardware limits, domain
topology, and common pitfalls of each family.

| Vendor | SoC families | Notes |
|--------|-------------|-------|
| Rockchip | RK3399, RK3568, RK3576, **RK3588**, RK3588S | GPIO banks, power domains, CRU |
| Allwinner | H616, H618, T507, A527 | AXP PMIC sequences, 1.8V IO banks |
| NXP | i.MX8M Plus, i.MX8M Mini | CCM, IOMUXC, power gating |
| Qualcomm | SDM845, SM8550 | GCC node dependency |
| Generic | any ARM/ARM64 | GIC, GPIO, DMA, PWM, IRQ rules |

Adding a new SoC requires only a YAML hardware-constraint file:

```yaml
# data/soc/rockchip/rk3588.yaml
name: rk3588
hardware_limits:
  gpio0: {pins: 32}
  gpio4: {pins: 32}
  dmac0: {channels: 32}
  pwm:   {channels: 16}
power_domains:
  - name: vcc_5v0
    children: [vcc_3v3, vcc_1v8]
```

---

## CI / CD integration

Drop socc into any pipeline:

```yaml
# GitHub Actions
- name: Check DTS consistency
  run: |
    pip install soc-consistency
    socc check board.dts --soc rk3588 --min-severity error --format json -o socc.json
  continue-on-error: false
```

socc exits with code 0 on success and non-zero when violations at or above
`--min-severity` are found.

---

## Rules reference

Run `socc rules` to list all active rules.  The full reference is in
[docs/rules.md](docs/rules.md).

Quick overview by domain:

| Prefix | Domain |
|--------|--------|
| `PD-` | Power domain integrity |
| `CK-` | Clock tree consistency |
| `GP-` | GPIO pin-mux conflicts |
| `MM-` | MMIO address-map overlaps |
| `BND-` | Physical resource bounds |
| `IRQ-` | Interrupt routing |
| `DG-` | Dependency graph cycles |
| `TH-` | Thermal zone configuration |
| `BW-` | Bus bandwidth budget |
| `SEC-` | Security / TrustZone constraints |

---

## Architecture

```
socc/
├── parser/        DTS → IR model (tokenizer + parser + mapper)
├── model/         Dataclasses: SoC, PowerTree, ClockTree, IRNode
├── rules/         Rule registry + vendor rule packs
│   ├── common/    GIC, GPIO, DMA, MMIO rules
│   └── rockchip/  Rockchip-specific power/clock/bus rules
├── engine/        Rule executor + violation aggregator
├── memmap.py      MMIO sweep-line overlap scanner
├── depgraph.py    Power/clock cycle detector (DFS)
├── smartdiff.py   Semantic DTS/DTB differ
├── gc.py          Zombie-node garbage collector
├── bounds.py      Physical resource bounds auditor
├── irqcheck.py    IRQ collision & routing checker
├── autofix.py     DTS patch generator
├── report.py      HTML report renderer
└── cli.py         Click-based CLI (43 commands)
```

The IR model is intentionally decoupled from the parser: you can load a
SoC model from a DTS file, a YAML hardware description, or build one
programmatically for testing.

---

## Contributing

```bash
git clone https://github.com/gahingwoo/SoC-Consistency.git
cd SoC-Consistency
pip install -e ".[dev]"
pytest tests/ -q
```

Rule contributions are welcome.  Each rule is a single Python class
inheriting `BaseRule`; see [docs/development.md](docs/development.md)
for the five-minute guide.

---

## License

MIT — see [LICENSE](LICENSE).
