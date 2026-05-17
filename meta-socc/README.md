# meta-socc

A Yocto/OpenEmbedded layer that adds SoC-Consistency hardware constraint
validation as a mandatory pre-build gate in your BSP build pipeline.

## Why

Yocto builds take **3–5 hours** on a 64-core server.  A single DTS typo
(1.8V written as 3.3V, wrong pinmux, missing power domain) burns through
that time and then **destroys hardware on first boot**.

`meta-socc` intercepts the build at `do_configure` — before a single line
of kernel code is compiled — and aborts immediately with a human-readable
error report.

```
ERROR: meta-socc hardware constraint check failed.
       Halting build to save compute time and prevent hardware damage.

[FATAL] Node: /soc/i2c@fe2b0000
  Supply vcc_3v3 (3.3V) is connected to a 1.8V IO domain.
  This will permanently destroy the I2C IO cells.
  Fix: change vcc-supply = <&vcc_3v3> to <&vcc_1v8_pmu>
```

## Quick Start

### 1. Add the layer

```bash
# In your build directory
bitbake-layers add-layer /path/to/meta-socc
```

### 2. Configure your machine

Add to `conf/local.conf` (or your machine `.conf`):

```bitbake
# Path to the board DTS file (required)
SOCC_DTS_FILE = "${TOPDIR}/../sources/meta-bsp/files/my-board.dts"

# SoC name — "auto" detects from filename (optional)
SOCC_SOC = "rk3588"

# Minimum severity to fail the build: "warning" | "error" | "fatal"
SOCC_SEVERITY = "error"

# Inherit the class in your image or BSP recipe
inherit socc
```

### 3. Build as usual

```bash
bitbake core-image-minimal
```

If the DTS passes all checks, `do_socc_check` completes silently and the
build proceeds.  If violations are found, the build is aborted with a
detailed report.

## Configuration Variables

| Variable         | Default  | Description |
|------------------|----------|-------------|
| `SOCC_DTS_FILE`  | *(none)* | **Required.** Path to the board `.dts` file |
| `SOCC_SOC`       | `auto`   | SoC name (`rk3588`, `imx8mp`, etc.) or `auto` |
| `SOCC_SEVERITY`  | `error`  | Minimum severity that fails the build |
| `SOCC_EXTRA_ARGS`| *(none)* | Extra flags forwarded to `socc check` |
| `SOCC_SKIP`      | `0`      | Set to `1` to skip the check (NOT recommended) |

## CI/CD Integration

```yaml
# GitHub Actions example
- name: Install socc
  run: pip install soc-consistency

- name: Check DTS before build
  run: socc check board.dts --soc rk3588 --min-severity warning
```

## Requirements

- **socc** must be installed on the build host:
  ```bash
  pip install soc-consistency
  ```
  Or use the included recipe to build it as a `native` package:
  ```bash
  bitbake socc-native
  ```

## Compatibility

| Yocto Release | Codename    | Status  |
|---------------|-------------|---------|
| 5.0           | Scarthgap   | ✅ Tested |
| 4.3           | Nanbield    | ✅ Tested |
| 4.2           | Mickledore  | ✅ Tested |
| < 4.2         | —           | Not officially supported |
