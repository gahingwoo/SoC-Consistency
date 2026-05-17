# socc.bbclass — SoC-Consistency hardware constraint gate
#
# Add to your machine recipe or BSP layer with:
#   inherit socc
#
# Required variables (set in your machine.conf or local.conf):
#   SOCC_DTS_FILE     — path to the board DTS, relative to ${TOPDIR}
#                       e.g. "${TOPDIR}/../sources/meta-bsp/recipes-bsp/u-boot/files/board.dts"
#
# Optional variables:
#   SOCC_SOC          — force SoC name (default: auto-detect from DTS filename)
#   SOCC_SEVERITY     — minimum severity to fail: "info"|"warning"|"error"|"fatal"
#                       (default: "error")
#   SOCC_EXTRA_ARGS   — extra arguments forwarded to `socc check`
#   SOCC_SKIP         — set to "1" to skip the check (not recommended in CI)
#
# Behaviour:
#   A new task `do_socc_check` is injected BEFORE `do_configure`.
#   If violations are found at or above SOCC_SEVERITY, BitBake reports
#   a fatal error with the full socc output and halts the build.
#
# SPDX-License-Identifier: MIT

SOCC_DTS_FILE    ??= ""
SOCC_SOC         ??= "auto"
SOCC_SEVERITY    ??= "error"
SOCC_EXTRA_ARGS  ??= ""
SOCC_SKIP        ??= "0"

# Locate the socc executable (must be available on the build host PATH)
SOCC_BIN = "${@bb.utils.which(d.getVar('PATH'), 'socc') or 'socc'}"

python do_socc_check() {
    import subprocess
    import os

    skip = d.getVar('SOCC_SKIP')
    if skip == "1":
        bb.warn("meta-socc: SOCC_SKIP=1, skipping hardware constraint check!")
        return

    dts_file = d.getVar('SOCC_DTS_FILE')
    if not dts_file:
        bb.warn("meta-socc: SOCC_DTS_FILE not set — cannot run hardware check. "
                "Set SOCC_DTS_FILE to the path of your board .dts file.")
        return

    if not os.path.exists(dts_file):
        bb.warn("meta-socc: DTS file not found: %s — skipping check." % dts_file)
        return

    soc       = d.getVar('SOCC_SOC')
    severity  = d.getVar('SOCC_SEVERITY')
    extra     = d.getVar('SOCC_EXTRA_ARGS')
    socc_bin  = d.getVar('SOCC_BIN')

    cmd = [socc_bin, "check", dts_file,
           "--soc", soc,
           "--min-severity", severity,
           "--no-color"]
    if extra:
        cmd += extra.split()

    bb.note("meta-socc: Running hardware constraint check: %s" % " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        bb.warn("meta-socc: 'socc' not found on PATH. "
                "Install with: pip install soc-consistency")
        return
    except subprocess.TimeoutExpired:
        bb.warn("meta-socc: socc check timed out after 120s.")
        return

    if result.returncode != 0:
        bb.fatal(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  meta-socc: HARDWARE CONSTRAINT FAILED — BUILD ABORTED      ║\n"
            "║  Fix the DTS violations below before re-running BitBake.    ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n"
            "\n%s\n"
            "ERROR: meta-socc hardware constraint check failed.\n"
            "       Halting build to save compute time and prevent hardware damage.\n"
            "       DTS: %s\n"
            % (result.stdout + result.stderr, dts_file)
        )
    else:
        bb.note("meta-socc: Hardware constraint check PASSED. Build continues.")
        if result.stdout.strip():
            bb.note("meta-socc output:\n%s" % result.stdout)
}

addtask do_socc_check before do_configure after do_fetch
do_socc_check[nostamp] = "1"
do_socc_check[doc] = "Run SoC-Consistency hardware constraint validation on the board DTS"
