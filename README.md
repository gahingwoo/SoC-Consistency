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
  - [Top-level](#top-level)
  - [socc audit — compatibility & BOM](#socc-audit--compatibility--bom)
  - [socc analyze — static analysis](#socc-analyze--static-analysis)
  - [socc generate — artifacts](#socc-generate--artifacts)
  - [socc viz — visualization](#socc-viz--visualization)
  - [socc sim — simulation & live-board](#socc-sim--simulation--live-board)
  - [socc socdef — constraint files](#socc-socdef--constraint-files)
- [Supported SoCs](#supported-socs)
- [CI / CD integration](#ci--cd-integration)
- [What's new in v1.2](#whats-new-in-v12)
- [What's new in v1.2 (continued)](#whats-new-in-v12-continued--developer-experience-features)
- [Upgrading from pre-1.1](#upgrading-from-pre-11)
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

socc exits 0 on success, non-zero when violations at or above `--min-severity` are found.

A ready-made GitHub Actions workflow with [OIDC Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
is included at [`.github/workflows/publish.yml`](.github/workflows/publish.yml).

---

## Upgrading from pre-1.1

All flat command names continue to work unchanged (they are registered as
hidden aliases).  No scripts need to be updated.

| Pre-1.1 command | 1.1+ equivalent |
|-----------------|----------------|
| `socc gc` | `socc analyze gc` |
| `socc check-memory` | `socc analyze memory` |
| `socc check-bounds` | `socc analyze bounds` |
| `socc check-irq` | `socc analyze irq` |
| `socc check-deps` | `socc analyze deps` |
| `socc audit` | `socc audit bindings` |
| `socc audit-bom` | `socc audit bom` |
| `socc amp-audit` | `socc audit amp` |
| `socc cross-check` | `socc audit cross-check` |
| `socc generate-qemu` | `socc generate qemu` |
| `socc generate-diagram` | `socc generate diagram` |
| `socc export-headers` | `socc generate headers` |
| `socc topology` | `socc viz topology` |
| `socc pinmap` | `socc viz pinmap` |
| `socc power-seq` | `socc viz power-seq` |
| `socc shell` | `socc sim shell` |
| `socc live-check` | `socc sim live-check` |
| `socc simulate-smoke` | `socc sim smoke` |
| `socc simulate failure NODE DTS` | `socc sim failure NODE DTS` |
| `socc migrate` | `socc sim migrate` |
| `socc validate-socdef` | `socc socdef validate` |
| `socc check-socdef` | `socc socdef check` |

---

## What's new in v1.2

### Rust-style diagnostics

Violation output now looks like a Rust compiler message, with a source-code
snippet and caret underline pointing to the exact DTS line:

```
error[GP-001]: GPIO pin conflict at gpio3/pin8
 --> arch/arm64/boot/dts/rockchip/rk3588-foo.dts:142:5
141 |     pinctrl-0 = <&uart2m1_xfer>;
142 |     pinctrl-0 = <&spi0_cs0>;
    |     ^^^^^^^^^^ Pin already claimed by uart2
    = hint: Remove the duplicate pinctrl-0 assignment.
```

No extra dependencies — uses only `click.style` for ANSI color.

### Fuzzy SoC name matching

Typos in `--soc` now produce helpful suggestions instead of a wall of choices:

```
$ socc check foo.dts --soc rk3589
Error: Unknown SoC 'rk3589'. Did you mean: rk3588, rk3588s, rk3576?
```

### Granular exit codes

`socc check` and `socc diff` now return precise exit codes for use in CI:

| Code | Meaning |
|------|---------|
| `0`  | No violations |
| `1`  | Info-level findings only |
| `2`  | At least one warning |
| `3`  | At least one error |

### Subsystem breakdown

The summary line now includes a per-domain count:

```
Subsystems: [GPIO:23  Power:12  Clock:8  IRQ:3]
Summary: 1 error(s), 12 warning(s), 23 info
```

### Watch mode

```bash
socc check rk3588-board.dts --soc rk3588 --watch
```

Re-runs automatically whenever the file changes.  Press Ctrl-C to stop.

### GitHub Actions annotations

```bash
socc check rk3588-board.dts --soc rk3588 --format annotations
# → ::error file=...,line=...,title=[GP-001]::GPIO pin conflict
```

### `socc diff --ci`

```bash
socc diff baseline.dts pr.dts --soc rk3588 --ci
```

In `--ci` mode the command exits non-zero on *any* regression (not just errors),
ideal for pull-request gates.

### Parse cache

DTS files are cached in `~/.cache/socc/` after the first parse.  Subsequent
runs with the same file are significantly faster.  Pass `--no-cache` to bypass.

```bash
socc check rk3588-board.dts --soc rk3588 --no-cache
```

### IPython shell

`socc sim shell` now launches IPython when it is installed, giving you a rich
interactive environment with tab-completion, history, and a pre-populated
namespace (`model`, `power`, `clock`, `devices`, `check()`, `pins`).

```bash
pip install ipython    # one-time
socc sim shell --demo
```

Falls back to the built-in REPL when IPython is not available.

---

## What's new in v1.2 (continued) — developer-experience features

### `socc bootstrap` — zero-cost SoC onboarding

Don't have a YAML constraint file for your SoC?  Point `socc bootstrap` at any
directory of Linux mainline `.dtsi` files and it will generate a working stub in
under two seconds:

```bash
socc bootstrap --from-mainline ./linux/arch/arm64/boot/dts/rockchip/ --soc rk3588
# → data/soc/rockchip/rk3588.yaml  (GPIO banks, clocks, IRQ controllers)
```

The generated file is fully editable.  Add datasheet-level constraints
(max GPIO index, clock ceiling, memory size) to get deeper checks.

### `socc viz pinmap --format xlsx` — Excel pin-assignment matrix

Hardware engineers live in Excel.  Export a fully formatted, colour-coded
pin-assignment spreadsheet that you can drop straight into a design document:

```bash
socc viz pinmap board.dts --soc rk3588 --format xlsx -o rk3588_pins.xlsx
socc viz pinmap board.dts --soc rk3588 --format csv  -o rk3588_pins.csv
```

Requires `openpyxl` (`pip install socc[xlsx]`).

### Inline `socc-ignore` suppression comments

Suppress a specific violation on the offending line without touching the
command line.  The comment is visible in code review and carries an optional
reason:

```dts
/* socc-ignore: BND-001 -- hardware errata, confirmed by vendor */
gpios = <&gpio4 35 GPIO_ACTIVE_HIGH>;
```

Multiple codes can be listed comma-separated: `/* socc-ignore: BND-001, GP-002 */`.

### `.socc_ignore` project-level exclusion file

Like `.gitignore` but for violations.  Commit it to your repo so the whole
team benefits:

```
# .socc_ignore
# Legacy layout — scheduled for Q3 hardware respin
BND-001  /soc/spi@fe610000
PD-006   *                   # suppress all orphaned-regulator warnings globally
GP-*     legacy/             # all GPIO rules in legacy/ subdirectory
```

Format: `CODE [PATH_GLOB]  [# optional comment]`

### Result cache — skip the rule engine when nothing changed

When the DTS file content is identical to the previous run, `socc check`
now skips parsing **and** rule execution entirely, reading cached violations
instead:

```
[cache] 2 violation(s) (content unchanged, skipped rule engine)
```

Invalidated automatically when any rule is added or the file changes.
Bypass with `--no-cache`.

### Custom rule plugin directory (`--rules-dir`)

Load company-internal rules without forking:

```bash
socc check board.dts --soc rk3588 --rules-dir ./acme_rules/
```

Every `*.py` file in the directory is imported.  If it exposes a
`register(registry)` function that function is called with the live registry.

```python
# acme_rules/power_budget.py
from socc.rules.base import Rule, Violation

class AcmePowerBudgetRule(Rule):
    code     = "ACME-001"
    name     = "Power budget"
    severity = "error"
    description = "Total power draw must not exceed 5 W"
    soc_targets = ["*"]

    def check(self, model, ctx):
        # … your logic …
        return []

def register(registry):
    registry.register(AcmePowerBudgetRule())
```

### `socc check --since REF` — git-aware incremental check

In CI, check only files changed since a git reference:

```bash
socc check board.dts --soc rk3588 --since origin/main
# → skipped if board.dts has no changes since origin/main
```

### `socc install-hook` — one-command git pre-commit integration

```bash
socc install-hook               # .git/hooks/pre-commit
socc install-hook --hook pre-push
socc install-hook --uninstall   # remove it
```

The hook automatically checks only **staged** DTS files, so it adds
milliseconds of latency for non-DTS commits.

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
