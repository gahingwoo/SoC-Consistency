# socc — SoC Device-Tree Consistency Checker

[![PyPI](https://img.shields.io/pypi/v/soc-consistency)](https://pypi.org/project/soc-consistency/)
[![Python](https://img.shields.io/pypi/pyversions/soc-consistency)](https://pypi.org/project/soc-consistency/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**socc** is a static-analysis and behavioural-simulation tool that catches
hardware-level bugs in Linux Device Tree Source (DTS) files — the kind that
pass `dtc` and `dt-schema` validation but still destroy your board at runtime.

If you have ever spent a week chasing a suspend-resume panic caused by a
wrong power-supply phandle, or a silent DMA hang caused by a copy-pasted
channel index that exceeded the controller's physical limit, socc is for you.

```text
$ socc check board.dts --soc rk3588
[FATAL]  PD-001  Power domain crossing — /soc/i2c@fe2b0000 uses vcc_3v3 (EE domain)
                  but is connected to vcc_1v8 (AO domain). System will panic on suspend.
[ERROR]  CK-003  Clock rate mismatch — spi0 requests 50 MHz from pll_cpll (max 24 MHz).
[WARN]   GP-002  GPIO conflict — gpio1_b3 assigned to both spi0 and uart2.

$ socc sim scenario board.dts --soc rk3588 --scenario suspend
Behavioral Simulation: rk3588  [board.dts]

  Scenario: suspend
  error[PS-002]  'vcc_io' disabled at t=4.5ms but uart0 not yet suspended
    Detail:  Active consumers: uart0
    Fix:     Ensure uart0 completes suspend callback before vcc_io is powered down.
  [UNSAFE] 1 error(s), 0 warning(s)  duration=6.5ms
```

---

## Table of Contents

- [Why socc?](#why-socc)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
  - [Top-level](#top-level)
  - [socc audit — compatibility & BOM](#socc-audit--compatibility--bom)
  - [socc analyze — static analysis](#socc-analyze--static-analysis)
  - [socc generate — artifacts](#socc-generate--artifacts)
  - [socc viz — visualization](#socc-viz--visualization)
  - [socc sim — simulation & live-board](#socc-sim--simulation--live-board)
  - [socc socdef — constraint files](#socc-socdef--constraint-files)
- [Supported SoCs](#supported-socs)
- [CI / CD integration](#ci--cd-integration)
- [Changelog](#changelog)
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
| Power rail off too early | vcc_io cut before uart0 suspend callback | Kernel panic on suspend |
| Clock gated with active consumer | pclk_uart gated while UART TX in flight | Data corruption / hang |
| Missing reset property | spi@ node has no `resets` | Driver cannot recover from warm reset |

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

# Semantic diff between two DTS / DTB files
socc smart-diff vendor.dts mainline.dts

# ── analyze group ──────────────────────────────────────────────────────────
socc analyze gc board.dts --soc rk3588          # zombie-node garbage collector
socc analyze memory board.dts                   # MMIO overlap scan
socc analyze bounds board.dts --soc rk3588      # GPIO/DMA/PWM out-of-bounds
socc analyze irq board.dts                      # IRQ collision check
socc analyze deps board.dts                     # power/clock cycle detection

# ── audit group ────────────────────────────────────────────────────────────
socc audit bindings board.dts                   # DTS binding audit
socc audit bom board.dts hardware.csv           # BOM cross-check
socc audit kernel board.dts --kernel /path/to/kernel  # kernel config audit

# ── viz group ─────────────────────────────────────────────────────────────
socc viz topology board.dts --soc rk3588        # HTML topology graph
socc viz pinmap rk3588                          # BGA pin heatmap
socc viz power-seq board.dts                    # power-rail sequencing chart

# ── sim group ─────────────────────────────────────────────────────────────
socc sim failure vcc_3v3 board.dts --soc rk3588 # FMEA blast radius
socc sim shell board.dts --soc rk3588           # interactive simulator
socc sim live-check board.dts --host 192.168.1.1 # live-board SSH audit
socc sim scenario board.dts --soc rk3588        # behavioural power/clock/reset sim
socc sim scenario --demo --scenario all         # try without hardware
```

---

## Commands

### Top-level

| Command | Description |
|---------|-------------|
| `socc check` | Run all rules — output violations by severity |
| `socc fix` | Auto-generate a DTS patch for safe-to-fix violations |
| `socc autofix` | Apply fixes in-place |
| `socc diff` | Show violations introduced between two DTS versions |
| `socc smart-diff` | Semantic DTS/DTB diff (ignores labels/phandles/ordering) |
| `socc explain` | Explain a rule code in plain English with fix examples |
| `socc rules` | List all enabled rules with IDs and descriptions |
| `socc init` | Create a `.socc.yaml` config scaffold |
| `socc version` | Print version and environment information |
| `socc self-update` | Upgrade to the latest release from PyPI |

#### `socc check` options

```bash
socc check board.dts --soc rk3588 \
     --min-severity warning \
     --format json \
     -o report.json
```

| Option | Description |
|--------|-------------|
| `--soc` | Target SoC (`rk3588`, `sun50i-h616`, `imx8mp`, …) |
| `--min-severity` | Filter: `info` \| `warning` \| `error` \| `fatal` |
| `--strict` | Exit non-zero for warnings too (default: only errors block CI) |
| `--format` | Output: `text` (default) \| `json` \| `html` \| `sarif` |
| `--netlist` | Cross-check against a KiCad or CSV netlist |
| `--enable` / `--disable` | Toggle specific rule codes |
| `-o` | Write output to file |

---

### socc audit — compatibility & BOM

```
socc audit COMMAND [OPTIONS] ...
```

| Subcommand | Old flat name | Description |
|------------|--------------|-------------|
| `socc audit bindings` | `socc audit` | DTS binding audit against mainline kernel |
| `socc audit bom` | `socc audit-bom` | Cross-check BOM CSV against DTS peripherals |
| `socc audit kernel` | `socc audit-kernel` | Validate DTS-enabled devices against kernel config |
| `socc audit amp` | `socc amp-audit` | AMP (Linux + RTOS) resource conflict detection |
| `socc audit matrix` | `socc matrix-audit` | Multi-SKU supply-chain variant matrix audit |
| `socc audit cross-check` | `socc cross-check` | Bootloader vs. kernel DTS skew detection |
| `socc audit overlay` | `socc overlay-check` | Validate a DT overlay against its base DTS |
| `socc audit netlist` | `socc crosscheck` | Cross-validate DTS pinctrl against PCB netlist |

---

### socc analyze — static analysis

```
socc analyze COMMAND [OPTIONS] DTS_FILE
```

| Subcommand | Old flat name | Description |
|------------|--------------|-------------|
| `socc analyze memory` | `socc check-memory` | Sweep-line MMIO overlap scanner |
| `socc analyze deps` | `socc check-deps` | Power/clock dependency cycle detector |
| `socc analyze bounds` | `socc check-bounds` | GPIO/DMA/PWM physical-bounds auditor |
| `socc analyze irq` | `socc check-irq` | IRQ collision and reserved-PPI checker |
| `socc analyze gc` | `socc gc` | Zombie-node garbage collector |

#### socc analyze gc

Vendor BSPs ship Device Trees with dozens of disabled nodes for unsupported
board variants.  `gc` identifies unreferenced dead-code nodes and estimates
DTB savings.

```bash
socc analyze gc rockchip-rk3588-evb.dts
```

```text
SOCC DTS ZOMBIE-NODE GARBAGE COLLECTOR
  Alive nodes   : 47
  Zombie nodes  : 14
  Est. DTB savings: 8732 bytes (~8.5 KiB)
```

#### socc analyze bounds

Catches copy-paste GPIO/DMA/PWM index errors that compile fine but panic at
driver probe:

```bash
socc analyze bounds board.dts --soc rk3588
```

```text
[FATAL] BND-001  /leds/user-led1
    gpios = ['&gpio4', 35, 0]  — pin 35 is out of range for gpio4 (max: 31)
```

#### socc analyze irq

Detects two active devices sharing the same non-shared GIC SPI line:

```bash
socc analyze irq board.dts
```

```text
[CRITICAL] IRQ-C01  GIC SPI IRQ 45 claimed by spi0 and uart0 simultaneously.
```

#### socc analyze memory

O(n log n) sweep-line detects every class of `reg` address-space collision:

| Rule | Description |
|------|-------------|
| `MM-001` | Identical window — duplicate node |
| `MM-002` | Full containment — one region inside another |
| `MM-003` | Partial overlap — silent memory corruption |
| `MM-004` | Zero-size region |
| `MM-005` | Suspiciously large region (> 512 MiB) |

#### socc analyze deps

Builds directed power/clock graphs and runs DFS cycle detection:

| Rule | Description |
|------|-------------|
| `DG-CP01` | Cycle in power dependency graph — kernel deadlock at boot |
| `DG-CK01` | Cycle in clock dependency graph |
| `DG-OP01` | Regulator references non-existent parent rail |
| `DG-OK01` | Clock references non-existent parent |

---

### socc generate — artifacts

```
socc generate COMMAND [OPTIONS] DTS_FILE
```

| Subcommand | Old flat name | Description |
|------------|--------------|-------------|
| `socc generate qemu` | `socc generate-qemu` | QEMU launch script or C machine skeleton |
| `socc generate tests` | `socc generate-tests` | Bash bring-up test script |
| `socc generate saleae` | `socc generate-saleae` | Saleae Logic 2 workspace JSON |
| `socc generate headers` | `socc export-headers` | Bare-metal C peripheral address header |
| `socc generate diagram` | `socc generate-diagram` | Mermaid / PlantUML / ASCII topology diagram |
| `socc generate compliance` | `socc generate-compliance` | ISO 26262 / IEC 61508 functional-safety report |
| `socc generate report` | `socc generate-report` | Self-contained HTML architecture report |
| `socc generate ci` | — | GitHub Actions / GitLab CI workflow for automated DTS checking |
| `socc generate docs` | — | Human-readable Markdown or HTML peripheral inventory from a DTS file |

---

### socc viz — visualization

```
socc viz COMMAND [OPTIONS]
```

| Subcommand | Old flat name | Description |
|------------|--------------|-------------|
| `socc viz topology` | `socc topology` | Interactive HTML hardware topology graph |
| `socc viz pinmap` | `socc pinmap` | HTML BGA pin heatmap for a SoC |
| `socc viz power-seq` | `socc power-seq` | ASCII power-rail startup sequence chart |

---

### socc sim — simulation & live-board

```
socc sim COMMAND [OPTIONS]
```

| Subcommand | Old flat name | Description |
|------------|--------------|-------------|
| `socc sim failure` | `socc simulate failure` | FMEA blast-radius simulation |
| `socc sim smoke` | `socc simulate-smoke` | Physical-damage risk from DTS config errors |
| `socc sim shell` | `socc shell` | Interactive power/clock state-machine shell |
| `socc sim scenario` | *(new in v1.3.0)* | Behavioural simulation of power/clock/reset sequencing |
| `socc sim live-check` | `socc live-check` | SSH into a board and run consistency checks |
| `socc sim live-probe` | `socc live-probe` | Compare DTS expectations vs. physical registers |
| `socc sim trace` | `socc trace` | Trace how a node's properties change across overlays |
| `socc sim migrate` | `socc migrate` | Assist in porting a DTS to a new target SoC |

---

### socc socdef — constraint files

```
socc socdef COMMAND [OPTIONS]
```

| Subcommand | Old flat name | Description |
|------------|--------------|-------------|
| `socc socdef validate` | `socc validate-socdef` | Validate a `.socdef` file |
| `socc socdef check` | `socc check-socdef` | Check a DTS against a `.socdef` constraint file |
| `socc socdef init` | `socc init-socdef` | Generate a `.socdef` template for a new SoC |

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
    socc check board.dts --soc rk3588 --min-severity error --format sarif -o socc.sarif
```

socc exits **0** on success, **3** when errors are present. By default, warnings do **not**
produce a non-zero exit — your pipeline won't fail just because a vendor BSP has
pre-existing style warnings.

Use `--strict` when you want warnings to block CI too:

```yaml
# GitHub Actions — strict mode
- name: Check DTS consistency
  run: |
    pip install soc-consistency
    socc check board.dts --soc rk3588 --strict --format sarif -o socc.sarif
```

| Exit code | Meaning (default) | Meaning (`--strict`) |
|-----------|-------------------|---------------------|
| `0` | Clean or warnings/info only | Clean |
| `2` | — | Warnings present |
| `3` | Errors present | Errors present |

A ready-made GitHub Actions workflow with [OIDC Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
is included at [`.github/workflows/publish.yml`](.github/workflows/publish.yml).

---

## Changelog

| Version | Highlights |
|---------|-----------|
| **v1.4.3** | Stabilization: JSON/SARIF output fixed (status → stderr); IRQ-C02 false positives eliminated (PPI 7 PMU, armv8-timer PPIs 13-15, GIC-v3 4-cell format); `__version__` sync |
| **v1.4.0** | 6 new rules (DMA-001/002, PD-007, THM-004/005, CK-107); `generate ci`, `generate docs`, `audit sku`, `check --binding` || **v1.3.1** | Fix SoC YAML data not bundled in wheel; path-traversal guard; renderer message fix |
| **v1.3.0** | Behavioural simulation engine (`socc sim scenario`) — PS/CG/RS violation codes |
| **v1.2.3** | Rule MM-006 (node-name/reg mismatch); `socc-expect` annotations; `socc decompile` |
| **v1.2.2** | `--strict` CI mode; `socc explain`; `smart-diff --semantic` |
| **v1.2.1** | Fix BW-101 false positive; CK-106 fixed-clock exemption |
| **v1.2.0** | Rust-style diagnostics; fuzzy SoC matching; watch mode; custom rule plugins |

Full release notes and upgrade guide: [docs/CHANGELOG.md](docs/CHANGELOG.md)

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
├── commands/          CLI command packages (new in 1.1)
│   ├── core.py        Top-level commands: check, fix, rules, diff, …
│   ├── audit_cmds.py  socc audit  — bindings, bom, kernel, amp, …
│   ├── analyze_cmds.py socc analyze — memory, bounds, irq, deps, gc
│   ├── generate_cmds.py socc generate — qemu, headers, diagram, …
│   ├── viz_cmds.py    socc viz    — topology, pinmap, power-seq
│   ├── sim_cmds.py    socc sim    — failure, smoke, shell, live-check, …
│   ├── socdef_cmds.py socc socdef — validate, check, init
│   └── _shared.py     Shared helpers: SoC lists, registry, helpers
├── parser/            DTS → IR model (tokenizer + parser + mapper)
├── model/             Dataclasses: SoC, PowerTree, ClockTree, IRNode
├── rules/             Rule registry + vendor rule packs
│   ├── common/        GIC, GPIO, DMA, MMIO rules
│   └── rockchip/      Rockchip-specific power/clock/bus rules
├── engine/            Rule executor + violation aggregator
├── memmap.py          MMIO sweep-line overlap scanner
├── depgraph.py        Power/clock cycle detector (DFS)
├── smartdiff.py       Semantic DTS/DTB differ
├── gc.py              Zombie-node garbage collector
├── bounds.py          Physical resource bounds auditor
├── irqcheck.py        IRQ collision & routing checker
├── autofix.py         DTS patch generator
├── report.py          HTML report renderer
└── cli.py             Click group assembler (152 lines) + backward aliases
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
