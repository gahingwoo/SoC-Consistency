# Changelog

All notable changes to **soc-consistency** are documented here.
Releases follow [Semantic Versioning](https://semver.org/).

---

## [1.4.3] — 2026-05-20

### Fixed

- **JSON/SARIF output corruption.** Status messages (`Loading device tree`,
  `[cache] …`) were written to stdout, prepending non-JSON text before the
  report and making `--format json` / `--format sarif` unparseable by downstream
  tools (e.g. `jq`, CI SARIF parsers).  Status messages now always go to stderr.
  *(socc/commands/_shared.py)*

- **IRQ-C02 false positive — ARM PMU (PPI 7).** `pmu-a55` / `pmu-a76` nodes
  were incorrectly flagged as using a reserved PPI.  PPI 7 is the architecturally
  assigned PMU interrupt and is perfectly valid for performance-counter drivers.
  *(socc/irqcheck.py — `_RESERVED_PPI`)*

- **IRQ-C02 false positive — ARM architectural timer (PPIs 13/14/15).**
  `arm,armv8-timer` nodes were flagged; PPIs 13–15 are the correct ARM
  architectural timer lines by specification.  These nodes are now exempted from
  the reserved-PPI check.
  *(socc/irqcheck.py — IRQ-C02 stage)*

- **IRQ-C02 false positive — 4-cell GIC interrupt format (GIC-v3 / SMMU).**
  `arm,smmu-v3` and other GIC-v3 peripherals encode interrupts as 4-cell tuples
  `<type  number  flags  affinity>`.  The parser consumed them as 3-cell triplets,
  misaligning subsequent entries and generating phantom IRQ collisions.  The
  parser now auto-detects stride-4 when the array length is a multiple of 4 but
  not 3.
  *(socc/irqcheck.py — `_parse_interrupts`)*

- **`__version__` mismatch.** `socc/__init__.py` reported `1.4.1` while
  `pyproject.toml` declared `1.4.2`; `socc --version` showed the wrong string.
  Both files now agree on `1.4.3`.

---

## [1.4.2] — 2026-05-20

### Fixed

- **Credibility — CK-107 suggestion text.**
  Suggestion messages no longer contain fabricated clock IDs (`CLK_USB3`) or
  hardcoded rates (`500000000`) that are irrelevant to HDMI, DSI, or PCIe
  devices. Both suggestion variants now say "manual review required" and direct
  the user to the SoC TRM and upstream kernel DTS.

- **False positives — DMA-001 / SEC-201 DMA master detection.**
  Replaced the broad substring token set with a strict allowlist of IP blocks
  that perform DMA directly (GPU, video codecs, ISP, NPU, USB host, PCIe,
  GMAC/STMMAC). Removed `"hdmi"`, `"i2s"`, `"spdif"`, `"dma"`, `"pl330"`,
  `"axi-dmac"`, `"display"`, `"drm"` — all of which caused false positives.
  Added exclusions for:
  - `grf` / `syscon` compatibles (config register banks, not DMA devices)
  - `-connector` compatibles (connector stubs)
  - `pl330` / `axi-dmac` compatibles (DMA engine controllers, not clients)
  - devices with a `dmas` property (they use the DMA engine; IOMMU belongs
    to the engine, not the client)

  Result: DMA-001 violations reduced from 26 → 9 (all true positives: GPU ×1,
  USB ×4, AV1-VPU ×1, PCIe ×2, GMAC ×1).

- **False positives — PD-006 `OrphanedRegulator` / power-domain sub-nodes.**
  `power-domain@N` sub-nodes on RK3588 have `#power-domain-cells = <0x00>`
  (they accept no specifier; they ARE domains, not providers). The mapper now
  only registers a node as a power-domain provider when
  `#power-domain-cells >= 1`. Previously the presence check caused all 28
  sub-nodes to be registered as providers, generating 26 spurious PD-006
  violations.

### Added

- **`tests/test_regression_fp.py`** — focused regression test file (14 tests)
  locking down the most credibility-critical false-positive fixes:
  DMA-001 exclusions (GRF, I2S/SPDIF, pl330, connectors), CK-107 token safety
  (fusb302, usbdpphy, -phy suffix), CK-107 suggestion quality, CK-106
  provider:spec skipping, PD-006 power-domain@N exclusion, PD-007 severity.

---

## [1.4.1] — 2026-05-20

### Fixed

- **False positives — MM-006 `RegisterAddressNodeNameMismatch`.**
  Rewrote `_all_addrs_from_reg()` to correctly handle multi-region `reg` arrays
  and 2-cell `<addr size>` forms. Previously, `usb2phy@0`, `codec-sram@0`,
  `video-codec@fdc38000`, and both `pcie@fe180000`/`fe190000` were flagged as
  mismatches; all were false positives caused by only reading the first region
  and misinterpreting 2-cell arrays as 64-bit hi/lo pairs.

- **False positives — CK-106 `ClockSourceContention`.**
  CRU/SCMI/CCF-managed clocks stored in `provider:spec` format are now skipped.
  Linux CCF handles multi-consumer sharing via reference counting; flagging 4
  USB controllers sharing the same CRU output as "contention" was incorrect.

- **False positives — CK-107 `AssignedClockRatesMissing`.**
  Removed `"usb3"` token (matched `fusb302` USB-PD chip via substring `"usb3"`
  in `"fusb302"`) and `"dp"` token (matched `csidphy-grf`, `usbdpphy-grf`,
  `hdptxphy-grf` syscon nodes and `csi-dphy`/`hdptx-phy` PHY nodes).
  Added explicit exclusions for `syscon`, `-connector`, and `-phy` compatibles.

- **False positives — CK-104 `ClockProviderOrphaned`.**
  Added `_link_device_clocks()` pass in `DTSMapper.map()` to populate
  `ClockProvider.outputs` from `device_clocks`. Providers such as the SCMI
  `protocol@14` clock controller were incorrectly flagged as orphaned even
  though many devices consumed their clocks.

- **False positives — PD-006 `OrphanedRegulator`.**
  Power-domain controller detection now requires `#power-domain-cells` in the
  node's properties rather than matching by name pattern. Previously all 28
  `power-domain@N` sub-nodes of the RK3588 power controller were incorrectly
  registered as standalone regulators and immediately flagged as orphaned.
  `regulator-state-*` sub-nodes (operating-mode descriptors) are also excluded.

- **False positives — DMA-001 / SEC-201.**
  `display-subsystem` virtual bus aggregator nodes are now excluded from DMA
  master checks; they are not DMA devices themselves.

- **Severity — PD-007 `IOBeforeCoreSequence` downgraded to `warning`.**
  PMIC sub-regulators powered directly from VIN do not declare an explicit DTS
  parent — this is the normal PMIC pattern. Raising these to `error` produced
  noise on every PMIC-based board.

- **Polish — SARIF output `your-org` placeholder URLs replaced.**
  `helpUri` and `informationUri` now point to
  `https://github.com/gahingwoo/SoC-Consistency`.

- **Polish — SARIF `version` field was hardcoded `"0.2.0"`.**
  Now reads from `socc.__version__` dynamically.

---

## [1.4.0] — 2026-05-20

### New Rules

- **DMA-001 `MissingIommuBinding`** — Detects DMA-capable devices (GPU, USB3, PCIe, VPU, GMAC …)
  that lack an `iommus` property when an IOMMU controller is present. Silent DMA without
  memory isolation is a common security hole on RK3588/i.MX8MP boards.
- **DMA-002 `IommuPhandelUndefined`** — Catches dangling `iommus = <&label>` references
  that do not resolve to any known IOMMU/SMMU controller node.
- **PD-007 `PowerDomainNamesMismatch`** — Flags devices where the number of entries in
  `power-domains` and `power-domain-names` disagrees. `of_pm_find_power_domain_dev()`
  silently returns NULL for unmatched entries, causing probe failure (ENODEV).
- **THM-004 `MissingThermalSensor`** — Thermal zone without a `thermal-sensors` binding;
  the kernel thermal driver cannot read temperature and the zone is inactive.
- **THM-005 `MissingCoolingDevice`** — Passive thermal zone with no `cooling-maps` entry;
  frequency scaling is never triggered and the zone reaches critical temp with no
  intermediate cooling step.
- **CK-107 `AssignedClockRatesMissing`** — USB3, PCIe, MIPI-DSI, HDMI, and other
  rate-critical devices that have `assigned-clocks` but are missing `assigned-clock-rates`
  will boot at the wrong frequency, causing silent malfunction.

### New Commands

- **`socc generate ci`** — Writes ready-to-use GitHub Actions (`.github/workflows/socc-check.yml`)
  or GitLab CI (`.gitlab-ci.yml`) configuration that gates every DTS PR through `socc check`.
  Options: `--platform github|gitlab|both`, `--strict`, `--soc`, `--dts-glob`.
- **`socc generate docs`** — Generates a structured Markdown or HTML peripheral inventory
  from a DTS file — base addresses, clocks, voltage rails, IRQs, GPIO assignments — for
  sharing with hardware teams. Options: `--format markdown|html`.
- **`socc audit sku`** — Multi-SKU DTS comparison: loads two or more board-variant DTS files,
  reports conflicting property values and nodes present in some SKUs but absent in others.
  Exits non-zero if any divergences are found. Options: `--format table|json`.
- **`socc check --binding`** — Invokes `dt-validate` (dtschema) if installed and appends its
  findings after socc's own violations, giving a combined socc + upstream binding audit in a
  single command.

---

## [1.3.1] — 2026-05-19

### Fixed

- **Critical — SoC YAML data not bundled in wheel distribution.**
  All 26 SoC constraint YAML files are now copied into `socc/data/soc/` and
  included via `package-data`.  Previously, `pyproject.toml` referenced a
  non-existent Python package named `data`, causing setuptools to silently
  omit every `.yaml` file from the wheel.  Users who installed from PyPI
  received an empty constraint set for all simulation runs.

- **Security — Path traversal in `find_soc_yaml()` and `_load_bga_data()`.**
  Both functions now validate the `soc_name` / `soc_lower` argument against
  `^[a-zA-Z0-9][a-zA-Z0-9_\-]*$` before constructing file paths, preventing
  a malicious `--soc ../../etc/passwd`-style argument from traversing outside
  the data directory (OWASP A01).

- **Data path resolution for installed packages.**
  `find_soc_yaml()` and `pinmap._load_bga_data()` now check the
  package-internal `socc/data/soc/` tree first (works for `pip install`),
  then fall back to the project-root `data/soc/` tree (works for editable /
  development installs).  The previous implementation only checked the project
  root, so wheel installs always fell back to an empty constraint set.

- **Renderer — misleading "No violations" message.**
  `render_text()` previously printed `"No {min_severity}-level violations
  found."` even though the severity filter is inclusive (e.g.
  `min_severity=warning` also includes errors).  Changed to the unambiguous
  `"No violations found."`.

### Notes

- The PS-003 check in `PowerStateMachine.simulate_boot()` is now documented
  with a clarifying comment: because `_topo_order()` always processes parent
  regulators before children, the check is unreachable in a well-formed power
  tree traversal.  It remains in place as a guard for callers that manipulate
  `states` externally before calling `simulate_boot()`.

---

## [1.3.0] — 2026-05-19

### Added — Behavioural simulation (`socc sim scenario`)

v1.3.0 introduces a full behavioural simulation engine that models the three
state machines a Linux kernel runs at every boot, suspend, and resume cycle:
**power rail sequencing**, **clock gate/ungate cascades**, and **reset
deassertion ordering**.

Static rules can only see what the DTS _says_. The simulation asks what
actually _happens_ at runtime.

```bash
socc sim scenario board.dts --soc rk3588
socc sim scenario board.dts --soc rk3588 --scenario suspend --timeline
socc sim scenario board.dts --soc rk3588 --format json
socc sim scenario --demo --scenario all
```

#### Scenarios

| Scenario | Description |
|----------|-------------|
| `boot` | Power-on and full device probe sequence |
| `suspend` | Linux PM suspend (s2idle / deep) |
| `resume` | Resume from suspend back to active |
| `runtime_pm` | Runtime PM autosuspend + wake cycle |
| `all` | Run all four in sequence (default) |

#### Violation codes

| Code | Severity | Scenario | Description |
|------|----------|----------|-------------|
| `PS-001` | warning | boot | Supply does not meet required stability window before consumer probes |
| `PS-002` | error | suspend | Supply disabled before consumer finishes suspend callback |
| `PS-003` | error | boot / resume | Child regulator enabled before parent is fully stable |
| `CG-001` | error | suspend | Clock gated while consumer device still active |
| `CG-002` | error | suspend | Parent clock disabled while child clock has active consumers |
| `RS-001` | error | boot | Device reset deasserted before required clock provider is ready |
| `RS-002` | warning | boot | Device missing required `resets` property |

#### Constraint-driven tuning

Timing requirements and ordering rules are read from the SoC YAML file
(`simulation_constraints` section).  The shipped rk3588 constraints cover
`power_sequencing`, `clock_gating`, `reset_dependencies`, and
`required_resets_patterns`.  Any custom SoC YAML can include the same section.

---

## [1.2.3] — 2026-05-18

### Added

#### Rule MM-006 — Register Address / Node-Name Mismatch

Catches copy-paste errors where the `@hex` suffix in a node name disagrees
with the first value of the `reg` property.  `dtc` compiles such files
without warnings; the kernel maps the driver to the wrong MMIO window.

```dts
/* BAD */
i2c@fe2b0000 { reg = <0xfe2c0000 0x1000>; /* MM-006 */ };
```

Both 32-bit and 64-bit cell pairs are handled.

#### Deliberate-violation annotations — `socc-expect`

Complements the existing `socc-ignore`.  An expected violation that does not
fire produces `info[SE-001]` so stale annotations are surfaced immediately:

```dts
/* socc-expect: MM-006 -- fly-wire on board rev B */
i2c@fe2b0000 { reg = <0xfe2c0000 0x1000>; };
```

#### Binary DTB decompiler — `socc decompile`

Annotates `dtc` decompile output with human-readable peripheral names from
the SoC database.  Accepts `.dtb` or `.dts` input:

```bash
socc decompile board.dtb --soc rk3588
socc decompile board.dtb --soc rk3588 -o board_annotated.dts
```

---

## [1.2.2] — 2026-05-18

### Added

#### `socc check --strict`

By default `socc check` exits 0 for warnings (only errors produce exit 3).
`--strict` restores full granular exit-code behaviour so CI can be configured
to treat warnings as failures.

#### Offline rule lookup — `socc explain CODE`

```bash
socc explain BW-101
```

Renders with `rich` when installed, falls back to plain text.

#### `socc smart-diff --semantic`

Filters the diff output to hardware-relevant property changes only —
clock frequencies, `reg`, supply rails, interrupt lines, pin-control settings,
etc.  Node ordering, labels, phandle renumbering, and non-critical metadata
are silently dropped.

---

## [1.2.1] — 2026-05-17

### Fixed

- **BW-101** — False positive on pinctrl subnodes no longer fires.
- **CK-106** — Fixed-clock nodes are now correctly exempted from clock-tree
  consistency checks.

---

## [1.2.0] — 2026-05-17

### Added

- **Rust-style diagnostics** — violation output now includes a source-code
  snippet and caret underline pointing to the exact DTS line.
- **Fuzzy SoC name matching** — typos in `--soc` produce helpful suggestions
  (`Did you mean: rk3588, rk3588s, rk3576?`).
- **Granular exit codes** — `socc check` and `socc diff` return 0 / 1 / 2 / 3
  for clean / info / warning / error respectively.
- **Subsystem breakdown** — summary line includes per-domain violation counts.
- **Watch mode** (`--watch`) — re-runs on file change.
- **GitHub Actions annotations** (`--format annotations`).
- **`socc diff --ci`** — exits non-zero on any regression.
- **Parse cache** in `~/.cache/socc/`.
- **IPython shell** — `socc sim shell` launches IPython when installed.
- **`socc bootstrap`** — generate a working SoC constraint stub from mainline
  `.dtsi` files in under two seconds.
- **`socc viz pinmap --format xlsx`** — Excel / CSV pin-assignment matrix
  (requires `openpyxl`, `pip install socc[xlsx]`).
- **Inline `socc-ignore` suppression comments** in DTS source.
- **`.socc_ignore` project-level exclusion file**.
- **Result cache** — skips rule engine when DTS content is unchanged.
- **Custom rule plugin directory** (`--rules-dir`).
- **`socc check --since REF`** — git-aware incremental check.
- **`socc install-hook`** — one-command git pre-commit integration.

---

## Upgrading from pre-1.1

All flat command names continue to work as hidden aliases.

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
