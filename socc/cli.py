"""Command-line interface."""

import click
from pathlib import Path
from typing import Optional

from socc.rules import RuleRegistry
from socc.engine import Checker
from socc.rules.rockchip import register_rockchip_rules
from socc.rules.allwinner import register_allwinner_rules, ALLWINNER_SOC_NAMES
from socc.rules.amlogic import register_amlogic_rules, AMLOGIC_SOC_NAMES
from socc.rules.qualcomm import register_qualcomm_rules, QUALCOMM_SOC_NAMES
from socc.rules.nxp import register_all_nxp_rules, NXP_SOC_NAMES
from socc.rules.common import register_common_rules
from socc.parser import build_sample_model, parse_dts_file
from socc.config import load_config, filter_by_severity, SAMPLE_CONFIG

@click.group()
@click.version_option(version="1.0.0", prog_name="socc")
def cli():
    """SoC device-tree consistency checker."""
    pass


# All recognized SoC names
_ROCKCHIP_SOCS = ["rk3588", "rk3576", "rk3568", "rk3566", "rk3399", "rk3328", "rk3528", "rk3308"]
_ALLWINNER_SOCS = sorted(ALLWINNER_SOC_NAMES)
_AMLOGIC_SOCS = sorted(AMLOGIC_SOC_NAMES)
_QUALCOMM_SOCS = sorted(QUALCOMM_SOC_NAMES)
_NXP_SOCS = sorted(NXP_SOC_NAMES)
_ALL_SOC_CHOICES = _ROCKCHIP_SOCS + _ALLWINNER_SOCS + _AMLOGIC_SOCS + _QUALCOMM_SOCS + _NXP_SOCS + ["auto"]


def _build_registry() -> RuleRegistry:
    """Build and return a fully populated rule registry (all vendors)."""
    registry = RuleRegistry()
    register_common_rules(registry)

    # Rockchip family
    for soc in _ROCKCHIP_SOCS:
        register_rockchip_rules(registry, soc)

    # Allwinner family
    for soc in _ALLWINNER_SOCS:
        register_allwinner_rules(registry, soc)

    # Amlogic family
    for soc in _AMLOGIC_SOCS:
        register_amlogic_rules(registry, soc)

    # Qualcomm family
    for soc in _QUALCOMM_SOCS:
        register_qualcomm_rules(registry, soc)

    # NXP family
    for soc in _NXP_SOCS:
        register_all_nxp_rules(registry, soc)

    return registry


def _auto_detect_soc(filename: str) -> str:
    """Infer SoC family from a DTS filename (lower-cased)."""
    fn = filename.lower()

    # Rockchip
    for chip in ["rk3588", "rk3576", "rk3568", "rk3566", "rk3399", "rk3328"]:
        if chip in fn:
            return chip

    # Allwinner
    if "sun50i-h616" in fn or "h616" in fn or "h618" in fn:
        return "sun50i-h616"
    if "sun50i-h6" in fn or "h6-" in fn or "-h6." in fn:
        return "sun50i-h6"
    if "sun50i-a64" in fn or "a64" in fn:
        return "sun50i-a64"
    if "sun8i-h3" in fn or "h3-" in fn or "-h3." in fn:
        return "sun8i-h3"
    if "sun8i-h2" in fn or "h2-plus" in fn:
        return "sun8i-h2-plus"
    if "sun50i-h5" in fn or "h5-" in fn:
        return "sun50i-h5"
    if "sun20i-d1" in fn or "d1-" in fn:
        return "sun20i-d1"
    if "sun55i-a527" in fn or "a527" in fn or "t527" in fn:
        return "sun55i-a527"
    if "sun55i-a733" in fn or "a733" in fn:
        return "sun55i-a733"
    if "sun55i-a523" in fn or "a523" in fn:
        return "sun55i-a523"

    # Qualcomm
    for chip in ["sdm845", "sm8250", "sm8350", "sm8450", "sc7180", "sc7280", "sc8280xp", "qcs6490", "qcs9100", "msm8998"]:
        if chip in fn:
            return chip

    # Amlogic
    if "gxbb" in fn or "s905." in fn or "s905-" in fn:
        return "meson-gxbb"
    if "gxl" in fn or "s905x" in fn or "s905d" in fn or "s905w" in fn:
        return "meson-gxl"
    if "gxm" in fn or "s912" in fn:
        return "meson-gxm"
    if "g12b" in fn or "s922x" in fn or "a311d2" not in fn and "a311d" in fn:
        return "meson-g12b"
    if "g12a" in fn or "s905x2" in fn or "s905d2" in fn:
        return "meson-g12a"
    if "sm1" in fn or "s905x3" in fn or "s905d3" in fn or "odroid-c4" in fn:
        return "meson-sm1"
    if "axg" in fn or "a113d" in fn or "a113x" in fn:
        return "meson-axg"
    if "t7" in fn or "a311d2" in fn:
        return "amlogic-t7"
    # Rockchip remaining (after longer names matched above)
    for chip in ["rk3528", "rk3308"]:
        if chip in fn:
            return chip

    # NXP i.MX family
    for chip in ["imx8mp", "imx8mq", "imx8mm", "imx8mn", "imx8ulp", "imx93", "imx95"]:
        if chip in fn:
            return chip
    if "imx8m" in fn:
        return "imx8mp"  # default to Plus variant when sub-variant unclear

    return "unknown"


def _severity_tag(severity: str, color: Optional[bool]) -> str:
    """Return a colored severity tag for terminal output."""
    tags = {
        "error": ("[E]", "red", True),
        "warning": ("[W]", "yellow", False),
        "info": ("[I]", "cyan", False),
    }
    label, fg, bold = tags.get(severity, ("[?]", "white", False))
    if color is False:
        return label
    return click.style(label, fg=fg, bold=bold)


def _echo(msg: str, color: Optional[bool] = None) -> None:
    """Print a status message with optional color stripping."""
    click.echo(msg)


@cli.command()
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC name (use 'auto' for filename-based auto-detection).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "sarif"]),
    default=None,
    help="Output format (text, json, or sarif for GitHub Code Scanning).",
)
@click.option(
    "--min-severity",
    type=click.Choice(["error", "warning", "info"]),
    default=None,
    help="Minimum severity level to report (error > warning > info).",
)
@click.option(
    "--ignore-rule",
    multiple=True,
    metavar="CODE",
    help="Suppress a rule code (can be repeated).",
)
@click.option(
    "--skip-rules",
    multiple=True,
    hidden=True,
    help="[Deprecated] Use --ignore-rule instead.",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off (default: auto-detect TTY).",
)
@click.option(
    "--demo",
    is_flag=True,
    help="Run in demo mode using built-in sample data.",
)
@click.option(
    "--netlist",
    "netlist_csv",
    type=click.Path(exists=True),
    default=None,
    metavar="CSV",
    help="Path to an EDA pin-assignment CSV (KiCad / Altium format) for "
         "netlist cross-checking (enables NET-601 / NET-602 rules).",
)
def check(
    dts_file: Optional[str],
    soc: Optional[str],
    output_format: Optional[str],
    min_severity: Optional[str],
    ignore_rule: tuple,
    skip_rules: tuple,
    color: Optional[bool],
    demo: bool,
    netlist_csv: Optional[str],
):
    """Check a device tree for SoC consistency violations."""

    # load config file defaults
    cfg = load_config()
    resolved_soc = soc or cfg.get("default_soc", "auto")
    resolved_format = output_format or cfg.get("format", "text")
    resolved_min_severity = min_severity or cfg.get("min_severity", "info")
    # merge ignore lists from config + CLI
    ignored = set(cfg.get("ignore_rules", [])) | set(ignore_rule) | set(skip_rules)
    # color: explicit flag > auto (True when stdout is a TTY)
    use_color = color if color is not None else None  # None means auto

    # initialize full rule registry (all vendors)
    registry = _build_registry()

    if demo:
        _echo("Running in demo mode...", color=use_color)
        model = build_sample_model("rk3588")
        soc_name = "rk3588"
    else:
        if not dts_file:
            click.echo("Error: specify a device-tree file or use --demo", err=True)
            return

        try:
            _echo(f"Loading device tree: {dts_file}", color=use_color)
            if resolved_soc == "auto":
                filename = Path(dts_file).name
                soc_name = _auto_detect_soc(filename)
            else:
                soc_name = resolved_soc

            model = parse_dts_file(dts_file, soc_name)
            _echo(f"Loaded device tree (SoC: {soc_name})", color=use_color)
        except FileNotFoundError:
            click.echo(f"Error: file not found: {dts_file!r}", err=True)
            return
        except Exception as e:
            click.echo(f"Error: failed to parse device tree: {e}", err=True)
            return

    # optional netlist cross-check
    extra_metadata: dict = {}
    if netlist_csv:
        try:
            from socc.netlist.parser import parse_netlist_csv
            netlist_pins = parse_netlist_csv(netlist_csv)
            extra_metadata["netlist_pins"] = netlist_pins
            _echo(
                f"Loaded netlist: {netlist_csv} ({len(netlist_pins)} pins)",
                color=use_color,
            )
        except Exception as e:
            click.echo(f"Warning: could not load netlist CSV: {e}", err=True)

    # run the checker
    checker = Checker(registry)
    violations = checker.check(model, soc_name, extra_metadata=extra_metadata)

    # filter: ignored rules
    if ignored:
        violations = [v for v in violations if v.code not in ignored]

    # filter: min severity
    violations = filter_by_severity(violations, resolved_min_severity)

    # generate report
    report = checker.generate_report(violations, resolved_format, color=use_color)
    click.echo(report)

    # non-zero exit when errors remain
    if any(v.severity == "error" for v in violations):
        raise SystemExit(1)


@cli.command()
def rules():
    """List all available rules (all vendors)."""
    registry = _build_registry()

    all_rules = registry.list_all_rules()
    all_rules.sort(key=lambda r: r.code)

    use_color = None  # auto

    click.echo("=" * 100)
    click.echo("SoC-Consistency Available Rules")
    click.echo("=" * 100)
    click.echo()

    for rule in all_rules:
        severity_symbol = _severity_tag(rule.severity, use_color)
        click.echo(f"{severity_symbol} [{rule.code}] {rule.name}")
        click.echo(f"  Description: {rule.description}")
        click.echo()


@cli.command()
def version():
    """Show version information."""
    click.echo("socc version 1.0.0")
    click.echo("SoC-Consistency Device Tree Checker")
    click.echo("Supported vendors: Rockchip, Allwinner, Amlogic, Qualcomm, NXP")


@cli.command()
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def fix(dts_file: str, soc: Optional[str], color: Optional[bool]):
    """Suggest automated fixes for common DTS violations.

    Prints a diff-style patch of suggested additions for each fixable
    violation.  The original file is never modified.
    """
    use_color = color

    cfg = load_config()
    registry = _build_registry()

    if soc is None or soc == "auto":
        soc_name = _auto_detect_soc(Path(dts_file).name)
    else:
        soc_name = soc

    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    checker = Checker(registry)
    violations = checker.check(model, soc_name)

    fixable = [v for v in violations if getattr(v, "suggestion", None)]
    if not fixable:
        click.echo("No auto-fixable violations found.")
        return

    if use_color is False:
        head = lambda s: s
        add = lambda s: s
    else:
        head = lambda s: click.style(s, fg="cyan", bold=True)
        add = lambda s: click.style(s, fg="green")

    click.echo(f"Found {len(fixable)} fixable violation(s) in {dts_file}:\n")
    for v in fixable:
        tag = _severity_tag(v.severity, use_color)
        click.echo(f"{tag} [{v.code}] {v.message}")
        click.echo(head("  --- Suggested fix ---"))
        for line in v.suggestion.splitlines():
            click.echo(add(f"  + {line}"))
        click.echo()


@cli.command()
@click.argument("before_dts", type=click.Path(exists=True))
@click.argument("after_dts", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def diff(before_dts: str, after_dts: str, soc: Optional[str], color: Optional[bool], output_format: str):
    """Show violations introduced between two DTS versions (regressions only).

    Compares BEFORE_DTS and AFTER_DTS; reports only violations present in
    AFTER but not in BEFORE.  Useful for CI: fail if a refactor adds errors.
    """
    use_color = color
    import json as _json

    registry = _build_registry()

    def _detect(path: str) -> str:
        if soc and soc != "auto":
            return soc
        return _auto_detect_soc(Path(path).name)

    def _run(path: str, soc_name: str):
        try:
            model = parse_dts_file(path, soc_name)
        except Exception as e:
            click.echo(f"Error parsing {path}: {e}", err=True)
            raise SystemExit(1)
        checker = Checker(registry)
        return checker.check(model, soc_name)

    soc_before = _detect(before_dts)
    soc_after = _detect(after_dts)
    violations_before = _run(before_dts, soc_before)
    violations_after = _run(after_dts, soc_after)

    # A violation is a regression if its (code, message) pair is new in AFTER
    before_keys = {(v.code, v.message) for v in violations_before}
    regressions = [v for v in violations_after if (v.code, v.message) not in before_keys]

    if output_format == "json":
        data = [
            {"code": v.code, "severity": v.severity, "message": v.message,
             "location": v.location, "suggestion": v.suggestion}
            for v in regressions
        ]
        click.echo(_json.dumps(data, indent=2))
        if any(v.severity == "error" for v in regressions):
            raise SystemExit(1)
        return

    if not regressions:
        if use_color is not False:
            click.echo(click.style("No regressions detected.", fg="green", bold=True))
        else:
            click.echo("No regressions detected.")
        return

    click.echo(f"Regressions introduced ({len(regressions)} new violation(s)):\n")
    for v in regressions:
        tag = _severity_tag(v.severity, use_color)
        loc = f"  at {v.location}" if v.location else ""
        click.echo(f"  {tag} [{v.code}] {v.message}{loc}")
        if v.suggestion:
            click.echo(f"       Fix: {v.suggestion.splitlines()[0]}")
        click.echo()

    if any(v.severity == "error" for v in regressions):
        raise SystemExit(1)


@cli.command("init")
@click.option(
    "--output",
    default=".socc.yaml",
    show_default=True,
    help="Destination path for the config file.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing config file.")
def init_cmd(output: str, force: bool):
    """Create a .socc.yaml config file with commented defaults."""
    dest = Path(output)
    if dest.exists() and not force:
        click.echo(f"Error: {output} already exists. Use --force to overwrite.", err=True)
        raise SystemExit(1)
    dest.write_text(SAMPLE_CONFIG)
    click.echo(f"Created {output}")


@cli.command("power-seq")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
@click.option(
    "--demo",
    is_flag=True,
    help="Use built-in sample model instead of a real DTS file.",
)
def power_seq(dts_file: Optional[str], soc: Optional[str], color: Optional[bool], demo: bool):
    """Visualise the power-rail startup sequence as an ASCII timing waveform.

    Reads the power tree from DTS_FILE (or the built-in demo model) and
    renders a horizontal waveform showing which supply comes online first.
    Startup delay is derived from the ``regulator-enable-ramp-delay`` DTS
    property; tree topology determines relative ordering.

    Example output::

      VIN_5V   ████████████████████████████████████  5.0V  [fixed]
      DCDC1    ░░░░░░░░░░░░████████████████████████  0.9V  [buck]  →VIN_5V
      LDO1     ░░░░░░░░░░░░░░░░░░░░░░░░████████████  3.3V  [ldo]   →VIN_5V
    """
    from socc.visualize.power_seq import render_power_sequence

    use_color = color

    if demo:
        model = build_sample_model("rk3588")
        title = "Power Rail Startup Sequence — demo (rk3588)"
    else:
        if not dts_file:
            click.echo("Error: specify a DTS file or use --demo", err=True)
            raise SystemExit(1)
        if soc is None or soc == "auto":
            soc_name = _auto_detect_soc(Path(dts_file).name)
        else:
            soc_name = soc
        try:
            model = parse_dts_file(dts_file, soc_name)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)
        title = f"Power Rail Startup Sequence — {Path(dts_file).name}"

    output = render_power_sequence(model, use_color=use_color, title=title)
    click.echo(output)


@cli.command("pinmap")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option(
    "--soc",
    "soc_name",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    required=True,
    help="SoC identifier (must match a YAML file with a 'bga:' section).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Output HTML file path (default: {soc_name}-pinmap.html).",
)
@click.option(
    "--no-dts",
    is_flag=True,
    help="Generate heatmap from YAML data only, without DTS cross-reference.",
)
def pinmap(
    dts_file: Optional[str],
    soc_name: str,
    output: Optional[str],
    no_dts: bool,
):
    """Generate an HTML BGA pinout heatmap for a SoC.

    Reads BGA ball-assignment data from the SoC YAML constraint file
    (``data/soc/{vendor}/{soc}.yaml``) and produces a self-contained HTML
    file with a colour-coded grid.  When a DTS_FILE is supplied, configured
    pins are cross-referenced and any mismatches highlighted in yellow.

    Colors: red=power, dark=ground, green=GPIO, blue=IO, purple=high-speed,
    orange=DDR, teal=audio, yellow=JTAG, grey=NC.

    Requires a ``bga:`` section in the SoC YAML file.
    """
    from socc.visualize.pinmap import render_bga_heatmap

    dts_pinmux: Optional[dict] = None
    if dts_file and not no_dts:
        try:
            model = parse_dts_file(dts_file, soc_name)
            dts_pinmux = model.pinmux_config or None
        except Exception as e:
            click.echo(f"Warning: could not parse DTS for cross-reference: {e}", err=True)

    out_path = output or f"{soc_name}-pinmap.html"

    try:
        result = render_bga_heatmap(
            soc_name=soc_name,
            output_path=out_path,
            dts_pinmux=dts_pinmux,
        )
        click.echo(f"Pinout heatmap written to: {result}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# topology — Interactive HTML hardware topology graph
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("topology")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Output HTML file path (default: {soc}-topology.html).",
)
@click.option(
    "--demo",
    is_flag=True,
    help="Use built-in sample model.",
)
@click.option(
    "--no-open",
    is_flag=True,
    help="Do not open the HTML in the browser after generation.",
)
@click.option(
    "--with-violations",
    is_flag=True,
    help="Run checker and annotate violations onto the graph.",
)
def topology(
    dts_file: Optional[str],
    soc: Optional[str],
    output: Optional[str],
    demo: bool,
    no_open: bool,
    with_violations: bool,
):
    """Generate an interactive HTML hardware topology graph.

    Renders the full power tree, clock tree, and device graph from DTS_FILE
    (or the built-in demo model) as an interactive, zoomable vis.js node-graph.

    Node colors: red=PMIC/root supply, orange=DCDC, amber=LDO, dark-blue=PLL,
    light-blue=clock, green=device.  Violations highlight the affected nodes
    and edges in red.

    Requires:  pip install pyvis
    """
    from socc.visualize.topology import render_topology_html
    import subprocess
    import sys

    if demo:
        model = build_sample_model("rk3588")
        soc_name = "rk3588"
    else:
        if not dts_file:
            click.echo("Error: specify a DTS file or use --demo", err=True)
            raise SystemExit(1)
        if soc is None or soc == "auto":
            soc_name = _auto_detect_soc(Path(dts_file).name)
        else:
            soc_name = soc
        try:
            model = parse_dts_file(dts_file, soc_name)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

    violations = []
    if with_violations:
        registry = _build_registry()
        checker = Checker(registry)
        violations = checker.check(model, soc_name)

    out_path = output or f"{soc_name}-topology.html"
    title = f"SoC Topology — {soc_name}"
    if dts_file:
        title += f" ({Path(dts_file).name})"

    try:
        result = render_topology_html(
            model=model,
            output_path=out_path,
            violations=violations,
            title=title,
        )
    except ImportError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    viol_note = f" ({len(violations)} violation(s) annotated)" if violations else ""
    click.echo(f"Topology graph written to: {result}{viol_note}")

    if not no_open:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", result])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", result])
            elif sys.platform == "win32":
                subprocess.Popen(["start", result], shell=True)
        except Exception:
            pass  # best-effort auto-open; ignore failures


# ─────────────────────────────────────────────────────────────────────────────
# cross-check — Cross-stage DTS validation (U-Boot vs Linux)
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("cross-check")
@click.argument("bootloader_dts", type=click.Path(exists=True))
@click.argument("kernel_dts", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default="auto",
    show_default=True,
    help="Target SoC (default: auto-detect from kernel DTS filename).",
)
@click.option(
    "--report",
    type=click.Choice(["text", "html", "json"]),
    default="text",
    show_default=True,
    help="Output report format.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Write report to FILE instead of stdout.",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def cross_check(
    bootloader_dts: str,
    kernel_dts: str,
    soc: str,
    report: str,
    output: Optional[str],
    color: Optional[bool],
):
    """Cross-validate Bootloader vs Linux DTS for critical node divergence.

    Parses BOOTLOADER_DTS (e.g. U-Boot or EDK2) and KERNEL_DTS (Linux),
    then compares them on critical categories:

    \b
    - UART / debug console (must match between stages)
    - Memory layout (base address + size)
    - Core power supplies (regulator voltage, enable order)
    - Primary clock sources (PLL configuration)
    - Pin-mux for console / debug / eMMC boot path

    Divergences in ADDED / REMOVED / CHANGED properties are reported.
    """
    from socc.crosscheck.comparator import compare_dts_stages, format_report

    use_color = color

    if soc == "auto":
        soc_name = _auto_detect_soc(Path(kernel_dts).name)
    else:
        soc_name = soc

    click.echo(f"Parsing bootloader DTS : {bootloader_dts}")
    click.echo(f"Parsing kernel DTS     : {kernel_dts}")
    click.echo(f"SoC                    : {soc_name}")
    click.echo()

    try:
        bl_model = parse_dts_file(bootloader_dts, soc_name)
        kn_model = parse_dts_file(kernel_dts, soc_name)
    except Exception as e:
        click.echo(f"Error parsing DTS: {e}", err=True)
        raise SystemExit(1)

    diffs = compare_dts_stages(bl_model, kn_model)
    result_text = format_report(
        diffs,
        fmt=report,
        use_color=use_color,
        bootloader_path=bootloader_dts,
        kernel_path=kernel_dts,
        soc_name=soc_name,
    )

    if output:
        Path(output).write_text(result_text, encoding="utf-8")
        click.echo(f"Report written to: {output}")
    else:
        click.echo(result_text)

    # exit 1 if any critical divergences
    critical = [d for d in diffs if d.severity == "error"]
    if critical:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# shell — Interactive power/clock state machine simulator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("shell")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--demo",
    is_flag=True,
    help="Use built-in sample model.",
)
def shell(
    dts_file: Optional[str],
    soc: Optional[str],
    demo: bool,
):
    """Interactive power/clock state machine simulator.

    Loads the SoC model and enters an interactive REPL where you can simulate
    power rail and clock state transitions and observe cascading effects.

    \b
    Available commands inside the shell:
      status              Show current rail/clock states
      tree                Print power tree topology
      turn_off <rail>     Simulate powering off a supply rail
      turn_on  <rail>     Simulate powering on a supply rail
      check               Re-run consistency rules on current state
      help                Show this help
      quit / exit / q     Exit the simulator
    """
    from socc.simulator.shell import PowerSimulator

    if demo:
        model = build_sample_model("rk3588")
        soc_name = "rk3588"
    else:
        if not dts_file:
            click.echo("Error: specify a DTS file or use --demo", err=True)
            raise SystemExit(1)
        if soc is None or soc == "auto":
            soc_name = _auto_detect_soc(Path(dts_file).name)
        else:
            soc_name = soc
        try:
            model = parse_dts_file(dts_file, soc_name)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

    sim = PowerSimulator(model, soc_name)
    sim.run()


# ─────────────────────────────────────────────────────────────────────────────
# overlay-check — DTO conflict simulator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("overlay-check")
@click.argument("base_dts", type=click.Path(exists=True))
@click.argument("overlays", type=click.Path(exists=True), nargs=-1, required=True)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default="auto",
    show_default=True,
    help="Target SoC (default: auto-detect from base DTS filename).",
)
@click.option(
    "--run-checks",
    is_flag=True,
    help="Also run all consistency rules on the merged tree after conflict detection.",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def overlay_check(
    base_dts: str,
    overlays: tuple,
    soc: str,
    run_checks: bool,
    color: Optional[bool],
):
    """Simulate merging Device Tree Overlays and detect conflicts.

    Applies each DTBO file onto BASE_DTS in order, replicating the Linux
    kernel's overlay-apply logic.  Reports property overrides, pin-mux
    conflicts, and address aliases that would cause hardware failures.

    \b
    Conflict types:
      PROPERTY_OVERRIDE  — two overlays set the same node property differently
      PINMUX_CONFLICT    — same physical pin assigned to different functions
      REG_ALIAS          — two nodes claim the same I2C/SPI bus address

    Use --run-checks to also run all SoC-specific consistency rules on the
    final merged tree (detects problems only visible after full merge).

    Example:
      socc overlay-check board.dts camera.dtbo display.dtbo --run-checks
    """
    from socc.overlay.merger import OverlayMerger

    use_color = color if color is not None else None

    if soc == "auto":
        soc_name = _auto_detect_soc(Path(base_dts).name)
    else:
        soc_name = soc

    click.echo(f"Base DTS  : {base_dts}")
    for i, ov in enumerate(overlays, 1):
        click.echo(f"Overlay {i} : {ov}")
    click.echo(f"SoC       : {soc_name}")
    click.echo()

    merger = OverlayMerger(base_dts)
    for ov in overlays:
        merger.add_overlay(ov)

    conflicts = merger.detect_conflicts()
    click.echo(merger.report(use_color=use_color is not False))

    if run_checks:
        click.echo("\nRunning consistency rules on merged tree …")
        try:
            model = merger.merged_model(soc_name=soc_name)
            registry = _build_registry()
            checker = Checker(registry)
            violations = checker.check(model, soc_name)
            if violations:
                click.echo(f"\n  {len(violations)} rule violation(s) on merged tree:")
                for v in violations:
                    tag = "[E]" if v.severity == "error" else "[W]"
                    click.echo(f"  {tag} [{v.code}] {v.message[:100]}")
            else:
                click.echo("  No rule violations on merged tree.")
        except Exception as e:
            click.echo(f"  Warning: could not run checks: {e}", err=True)

    if any(c.severity == "error" for c in conflicts):
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# live-check — SSH live target extraction and check
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("live-check")
@click.argument("target")
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default="auto",
    show_default=True,
    help="Target SoC (default: auto-detect from device model string).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "sarif"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--min-severity",
    type=click.Choice(["error", "warning", "info"]),
    default="warning",
    show_default=True,
    help="Minimum severity level to report.",
)
@click.option(
    "--save-dts",
    default=None,
    metavar="FILE",
    help="Save the extracted DTS to FILE (default: discard after check).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def live_check(
    target: str,
    soc: str,
    output_format: str,
    min_severity: str,
    save_dts: Optional[str],
    color: Optional[bool],
):
    """Connect to a live board via SSH and run consistency checks.

    TARGET is ``user@host`` or ``user@host:port``, e.g. ``root@192.168.1.50``.

    \b
    What happens under the hood:
      1. SSH into TARGET (OpenSSH client required on local machine).
      2. Read /sys/firmware/fdt — the live FDT blob from the running kernel.
      3. Decompile DTB → DTS using dtc (local or remote install).
      4. Parse and run all SoC consistency rules in memory.
      5. Print the violation report.

    \b
    Requirements:
      - OpenSSH client on local host (ssh / scp)
      - dtc (device-tree-compiler) on local host:
          brew install dtc          (macOS)
          apt-get install device-tree-compiler  (Linux)
      - SSH access to TARGET with read permission on /sys/firmware/fdt
        (typically needs root or a user in the 'sudo' group)

    Example:
      socc live-check root@192.168.1.50 --min-severity warning
    """
    from socc.live.connector import extract_live_dts
    from socc.config import filter_by_severity

    use_color = color

    click.echo(f"Connecting to {target} …")

    try:
        model, dts_path = extract_live_dts(target, soc_name=soc)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    soc_name = model.name
    click.echo(f"Extracted live FDT from {target}  (SoC: {soc_name})")
    click.echo(f"Temporary DTS: {dts_path}")

    if save_dts:
        import shutil
        shutil.copy2(dts_path, save_dts)
        click.echo(f"DTS saved to: {save_dts}")

    click.echo()

    registry = _build_registry()
    checker = Checker(registry)
    violations = checker.check(model, soc_name)
    violations = filter_by_severity(violations, min_severity)

    report = checker.generate_report(violations, output_format, color=use_color)
    click.echo(report)

    if any(v.severity == "error" for v in violations):
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# self-update — upgrade socc to the latest version from PyPI
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("self-update")
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check for a newer version; do not install.",
)
def self_update(check_only: bool):
    """Upgrade socc to the latest version from PyPI.

    Fetches the latest release metadata from https://pypi.org/pypi/soc-consistency/
    and, if a newer version is available, runs:

      pip install --upgrade soc-consistency

    Use --check-only to see whether an update is available without installing.
    """
    import json
    import sys
    import subprocess
    import urllib.request
    from socc import __version__

    pypi_url = "https://pypi.org/pypi/soc-consistency/json"
    click.echo(f"Current version : {__version__}")
    click.echo(f"Checking PyPI   : {pypi_url}")

    try:
        req = urllib.request.Request(pypi_url, headers={"User-Agent": f"socc/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        latest = data["info"]["version"]
    except Exception as e:
        click.echo(f"Could not reach PyPI: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"Latest version  : {latest}")

    def _ver_tuple(v: str):
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0,)

    if _ver_tuple(latest) <= _ver_tuple(__version__):
        click.echo(click.style("Already up-to-date.", fg="green", bold=True))
        return

    click.echo(click.style(f"\nNew version available: {latest}", fg="yellow", bold=True))

    if check_only:
        click.echo("Run 'socc self-update' (without --check-only) to install.")
        return

    click.echo(f"Installing socc {latest} …\n")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "soc-consistency"],
        check=False,
    )
    if result.returncode == 0:
        click.echo(click.style(f"\nUpgraded to socc {latest} successfully.", fg="green", bold=True))
        click.echo("Restart the terminal for the new version to take effect.")
    else:
        click.echo("pip upgrade failed.  Try manually:", err=True)
        click.echo("  pip install --upgrade soc-consistency", err=True)
        raise SystemExit(1)


@cli.command("audit")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--target",
    type=click.Choice(["mainline"]),
    default="mainline",
    show_default=True,
    help="Target kernel tree to audit against.",
)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC name (use 'auto' for filename-based auto-detection).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off (default: auto-detect TTY).",
)
def audit(
    dts_file: str,
    target: str,
    soc: Optional[str],
    output_format: str,
    color: Optional[bool],
):
    """Audit DTS bindings for compatibility with a target kernel tree.

    Scans every compatible string in DTS_FILE against a curated database of
    vendor-BSP-specific bindings that were renamed, split, or removed during
    mainline upstreaming.  Reports which nodes need updating and what the
    correct mainline binding is.

    \b
    Examples:
        socc audit board.dts
        socc audit board.dts --target mainline --format json
        socc audit board.dts --soc rk3588
    """
    import json as _json

    use_color = color  # None → auto

    # Detect SoC from filename if needed
    resolved_soc = soc or "auto"
    if resolved_soc == "auto":
        resolved_soc = _auto_detect_soc(Path(dts_file).name)

    # Parse DTS
    try:
        _echo(f"Loading device tree: {dts_file}", color=use_color)
        model = parse_dts_file(dts_file, resolved_soc)
        _echo(f"Parsed OK (SoC: {resolved_soc})", color=use_color)
    except Exception as exc:
        click.echo(f"Error: failed to parse device tree: {exc}", err=True)
        raise SystemExit(1)

    # Run only COMP rules
    from socc.rules.common.compat_rules import COMP101DeprecatedVendorBinding
    from socc.rules.base import CheckContext

    rule = COMP101DeprecatedVendorBinding()
    context = CheckContext(soc_name=resolved_soc)
    findings = rule.check(model, context)

    # ── output ────────────────────────────────────────────────────────────────
    if output_format == "json":
        out = {
            "dts_file": str(dts_file),
            "target": target,
            "soc": resolved_soc,
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "impact": f.impact,
                    "suggestion": f.suggestion,
                    "location": f.location,
                    "affected_nodes": f.affected_nodes,
                }
                for f in findings
            ],
        }
        click.echo(_json.dumps(out, indent=2))
        if findings:
            raise SystemExit(1)
        return

    # Text output
    if not findings:
        click.echo(
            click.style(
                f"\n✓ No deprecated vendor bindings found in {dts_file}",
                fg="green",
                bold=True,
            )
        )
        click.echo(
            f"  All compatible strings are compatible with mainline Linux {target} tree."
        )
        return

    click.echo(
        click.style(
            f"\nVendor-to-Mainline Audit: {Path(dts_file).name}",
            fg="cyan",
            bold=True,
        )
    )
    click.echo(
        f"  Target: mainline Linux  |  SoC: {resolved_soc}  "
        f"|  Findings: {len(findings)}\n"
    )

    for i, v in enumerate(findings, 1):
        sev_color = {"error": "red", "warning": "yellow", "info": "blue"}.get(
            v.severity, "white"
        )
        click.echo(
            click.style(f"  [{i}] {v.code}", fg=sev_color, bold=True)
            + f"  {v.message}"
        )
        click.echo(f"       Impact:     {v.impact}")
        click.echo(
            click.style(f"       Fix:        {v.suggestion}", fg="green")
        )
        if v.location:
            click.echo(f"       Location:   {v.location}")
        click.echo()

    click.echo(
        click.style(
            f"  {len(findings)} deprecated binding(s) require updating for mainline.",
            fg="yellow",
            bold=True,
        )
    )
    click.echo(
        "  Run 'socc check' to see all consistency violations across rule categories."
    )
    raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# simulate failure — FMEA blast-radius engine
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("simulate")
@click.argument("action", type=click.Choice(["failure"]))
@click.argument("node_name")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def simulate(
    action: str,
    node_name: str,
    dts_file: str,
    soc: Optional[str],
    color: Optional[bool],
):
    """Simulate a hardware failure and compute blast radius.

    \b
    Usage:
        socc simulate failure <node> <dts_file>

    Performs an FMEA (Failure Mode and Effects Analysis) on NODE_NAME
    (a regulator or device name from DTS_FILE) and reports all cascading
    power failures, offline devices, frozen clocks, and a final severity
    verdict (SAFE / DEGRADED / CRITICAL / FATAL).

    \b
    Examples:
        socc simulate failure vcc_3v3_sys board.dts
        socc simulate failure /soc/i2c@fe2b0000/pmic@1b board.dts
    """
    from socc.fmea import simulate_failure, render_fmea_report

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    report = simulate_failure(model, node_name)
    click.echo(render_fmea_report(report, use_color=color))

    if report.severity in ("FATAL", "CRITICAL"):
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# audit-bom — BOM vs DTS cross-reference
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("audit-bom")
@click.argument("dts_file", type=click.Path(exists=True))
@click.argument("bom_csv", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def audit_bom(
    dts_file: str,
    bom_csv: str,
    soc: Optional[str],
    output_format: str,
    color: Optional[bool],
):
    """Cross-reference a hardware BOM against the device tree.

    Reads BOM_CSV (factory pick-and-place or purchase-order CSV) and
    compares each chip's expected compatible string and bus address against
    what is declared in DTS_FILE.  Reports mismatches that would cause
    driver probe failures at boot.

    \b
    Examples:
        socc audit-bom board.dts hardware_bom.csv
        socc audit-bom board.dts bom.csv --format json
    """
    import json as _json
    from socc.bom import parse_bom_csv, audit_bom as _audit_bom, render_bom_report

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error parsing DTS: {exc}", err=True)
        raise SystemExit(1)

    try:
        bom_entries = parse_bom_csv(bom_csv)
    except Exception as exc:
        click.echo(f"Error reading BOM CSV: {exc}", err=True)
        raise SystemExit(1)

    violations = _audit_bom(model, bom_entries)

    if output_format == "json":
        import dataclasses
        click.echo(_json.dumps(
            [dataclasses.asdict(v) for v in violations],
            indent=2,
        ))
    else:
        click.echo(render_bom_report(violations, bom_csv, dts_file, use_color=color))

    criticals = [v for v in violations if v.severity == "CRITICAL"]
    if criticals:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# generate-tests — Bring-up test script generator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("generate-tests")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Write script to FILE (default: bring_up.sh).",
)
def generate_tests(
    dts_file: str,
    soc: Optional[str],
    output: Optional[str],
):
    """Generate a bash bring-up test script from a DTS file.

    Reads DTS_FILE and produces a self-contained bash script that verifies
    each enabled peripheral (I2C devices, UARTs, regulators, GPIOs, video
    devices, etc.) using standard Linux userspace tools.

    \b
    Examples:
        socc generate-tests board.dts
        socc generate-tests board.dts --output rock5b_bringup.sh
    """
    from socc.codegen.bringup import generate_tests as _gen_tests

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    script = _gen_tests(model, dts_name=Path(dts_file).name)
    out_path = output or "bring_up.sh"
    Path(out_path).write_text(script, encoding="utf-8")
    click.echo(f"Bring-up test script written to: {out_path}")
    click.echo(f"Run on target:  chmod +x {out_path} && sudo ./{out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# generate-saleae — Logic analyzer workspace generator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("generate-saleae")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Output JSON file (default: <dts_basename>_saleae.json).",
)
def generate_saleae(
    dts_file: str,
    soc: Optional[str],
    output: Optional[str],
):
    """Generate a Saleae Logic 2 workspace JSON from a DTS file.

    Scans DTS_FILE for enabled I2C, SPI, UART, and CAN buses and produces
    a Saleae Logic 2 workspace preset with pre-labelled channels and
    protocol analyzers at the correct bit-rate.

    Import via: Logic 2 → File → Load Existing Capture / Setup

    \b
    Examples:
        socc generate-saleae board.dts
        socc generate-saleae board.dts --output debug_session.json
    """
    from socc.codegen.saleae import render_saleae_workspace

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    json_str = render_saleae_workspace(model, dts_name=Path(dts_file).name)
    out_path = output or (Path(dts_file).stem + "_saleae.json")
    Path(out_path).write_text(json_str, encoding="utf-8")
    click.echo(f"Saleae Logic 2 workspace written to: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# amp-audit — AMP cross-core conflict detector
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("amp-audit")
@click.argument("linux_dts", type=click.Path(exists=True))
@click.argument("rtos_dts", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from Linux DTS filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def amp_audit(
    linux_dts: str,
    rtos_dts: str,
    soc: Optional[str],
    color: Optional[bool],
):
    """Detect resource conflicts in an AMP (Linux + RTOS) configuration.

    Parses LINUX_DTS and RTOS_DTS independently and cross-references them
    for shared IRQs, GPIO pin conflicts, power-domain gating conflicts, and
    overlapping MMIO register ranges that would cause the two OS images to
    fight over hardware.

    \b
    Examples:
        socc amp-audit linux.dts zephyr.dts
        socc amp-audit rock5b_linux.dts freertos_m0.dts --soc rk3588
    """
    from socc.amp import amp_audit as _amp_audit, render_amp_report

    soc_name = soc or _auto_detect_soc(Path(linux_dts).name)
    try:
        linux_model = parse_dts_file(linux_dts, soc_name)
        rtos_model  = parse_dts_file(rtos_dts, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    conflicts = _amp_audit(linux_model, rtos_model)
    click.echo(render_amp_report(conflicts, linux_dts, rtos_dts, use_color=color))

    fatals = [c for c in conflicts if c.severity == "FATAL"]
    if fatals:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# trace — DTBO property trace engine
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("trace")
@click.argument("node_path")
@click.argument("base_dts", type=click.Path(exists=True))
@click.argument("overlays", type=click.Path(exists=True), nargs=-1)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from base DTS filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def trace(
    node_path: str,
    base_dts: str,
    overlays: tuple,
    soc: Optional[str],
    color: Optional[bool],
):
    """Trace how a DTS node's properties change across overlay layers.

    Shows each property's value at every stage of the overlay stack so you
    can spot silent overrides — e.g., a camera DTBO re-disabling an I2C bus
    that the board file previously enabled.

    \b
    Examples:
        socc trace /soc/i2c@fe2b0000 base.dts
        socc trace /soc/i2c@fe2b0000 base.dts board.dts camera.dtbo
    """
    from socc.trace import trace_node, render_trace_report

    soc_name = soc or _auto_detect_soc(Path(base_dts).name)
    report = trace_node(
        node_path=node_path,
        base_dts=base_dts,
        overlays=list(overlays),
        soc_name=soc_name,
    )
    click.echo(render_trace_report(report, use_color=color))


# ─────────────────────────────────────────────────────────────────────────────
# migrate — Cross-platform DTS migration assistant
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("migrate")
@click.option(
    "--from",
    "from_dts",
    type=click.Path(exists=True),
    required=True,
    metavar="OLD_DTS",
    help="Source board DTS (old SoC).",
)
@click.option(
    "--to",
    "to_dts",
    type=click.Path(exists=True),
    default=None,
    metavar="NEW_BASE_DTS",
    help="Target SoC base DTS (optional — used for bus discovery).",
)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="New target SoC name (default: auto-detect from --to filename).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Write migration report to FILE (default: stdout).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
def migrate(
    from_dts: str,
    to_dts: Optional[str],
    soc: Optional[str],
    output: Optional[str],
    color: Optional[bool],
):
    """Assist in migrating a board DTS to a new target SoC.

    Reads the old board DTS (--from), extracts all defined peripherals,
    then attempts to map each one onto the new SoC DTS (--to).  Produces
    a report with:
      ✅ AUTO-MAPPED  — same peripheral found on same bus
      ⚠  NEEDS REVIEW — bus or address differs; manual adjustment needed
      ❌ UNMAPPABLE   — no suitable bus in the new SoC
      ℹ  INFO         — compatible string renaming suggestions

    \b
    Examples:
        socc migrate --from rk3399_board.dts --to rk3588.dtsi
        socc migrate --from old_board.dts --soc rk3588
    """
    from socc.migrate import migrate_dts, render_migration_report

    old_soc_name = _auto_detect_soc(Path(from_dts).name)

    if to_dts:
        new_soc_name = soc or _auto_detect_soc(Path(to_dts).name)
    else:
        new_soc_name = soc or "unknown"

    try:
        old_model = parse_dts_file(from_dts, old_soc_name)
    except Exception as exc:
        click.echo(f"Error parsing source DTS: {exc}", err=True)
        raise SystemExit(1)

    if to_dts:
        try:
            new_model = parse_dts_file(to_dts, new_soc_name)
        except Exception as exc:
            click.echo(f"Error parsing target DTS: {exc}", err=True)
            raise SystemExit(1)
    else:
        # Build an empty model as a placeholder
        from socc.parser import build_sample_model
        new_model = build_sample_model(new_soc_name)

    report = migrate_dts(old_model, new_model, old_dts=from_dts, new_base=to_dts or "")
    text = render_migration_report(report, use_color=color)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Migration report written to: {output}")
    else:
        click.echo(text)

    if report.unmappable:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# generate-qemu — Zero-hardware QEMU VM generator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("generate-qemu")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["cmd", "machine-c"]),
    default="cmd",
    show_default=True,
    help=(
        "cmd: shell launch script (default).  "
        "machine-c: skeleton QEMU C machine file."
    ),
)
@click.option(
    "--kernel", default="Image", show_default=True,
    metavar="FILE",
    help="Kernel image name used in the launch script.",
)
@click.option(
    "--dtb", default="board.dtb", show_default=True,
    metavar="FILE",
    help="DTB filename used in the launch script.",
)
@click.option(
    "--output", "-o", default=None, metavar="FILE",
    help="Write output to FILE instead of stdout.",
)
def generate_qemu(
    dts_file: str,
    soc: Optional[str],
    fmt: str,
    kernel: str,
    dtb: str,
    output: Optional[str],
):
    """Generate a QEMU launch script (or C machine skeleton) from a DTS file.

    Extracts CPU type, RAM layout, UART, GIC, and clock information from
    DTS_FILE and produces a ready-to-run ``qemu-system-aarch64`` invocation.

    This lets students boot Linux (or bare-metal code) on a virtual
    representation of the board *before* physical hardware arrives.

    \b
    Examples:
        socc generate-qemu board.dts
        socc generate-qemu board.dts --format machine-c --output hw/arm/myboard.c
        socc generate-qemu board.dts --kernel Image --dtb board.dtb -o launch.sh
    """
    from socc.qemu import build_qemu_spec, render_qemu_command, render_qemu_machine_c

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    spec = build_qemu_spec(model)

    if fmt == "machine-c":
        text = render_qemu_machine_c(spec)
        default_out = Path(dts_file).stem + "_machine.c"
    else:
        text = render_qemu_command(spec, kernel=kernel, dtb=dtb)
        default_out = "launch_qemu.sh"

    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"QEMU config written to: {output}")
    else:
        out_path = default_out
        Path(out_path).write_text(text, encoding="utf-8")
        click.echo(text)
        click.echo(f"\n# Written to: {out_path}", err=True)


# ─────────────────────────────────────────────────────────────────────────────
# simulate-smoke — Physical damage / "will it smoke?" simulator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("simulate-smoke")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force colored output on or off.",
)
@click.option(
    "--output", "-o", default=None, metavar="FILE",
    help="Write the casualty report to FILE.",
)
def simulate_smoke(
    dts_file: str,
    soc: Optional[str],
    color: Optional[bool],
    output: Optional[str],
):
    """Simulate physical hardware damage from DTS configuration errors.

    Analyses voltage-domain mismatches, clock overspeeds, and missing
    power-sequencing that could destroy real hardware.  Outputs a
    timestamped 'Casualty Report' explaining *what* will burn, *when*,
    and *why* — based on Ohm's law and thermal physics.

    Exit code 1 if any FATAL or CRITICAL events are found.

    \b
    Examples:
        socc simulate-smoke board.dts
        socc simulate-smoke board.dts --no-color -o casualty_report.txt
    """
    from socc.smoke import simulate_smoke as _sim_smoke, render_smoke_report

    use_color = color if color is not None else True
    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    sim = _sim_smoke(model, dts_path=dts_file)
    text = render_smoke_report(sim, use_color=use_color)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Casualty report written to: {output}")
    else:
        click.echo(text)

    if not sim.is_safe:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# export-headers — Bare-metal C header extractor
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("export-headers")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--output", "-o", default=None, metavar="FILE",
    help="Output .h file (default: <dts_basename>_hw.h, or stdout with --stdout).",
)
@click.option(
    "--stdout", "to_stdout", is_flag=True, default=False,
    help="Print header to stdout instead of writing a file.",
)
@click.option(
    "--no-regulators", "skip_regulators", is_flag=True, default=False,
    help="Omit regulator voltage constants.",
)
def export_headers(
    dts_file: str,
    soc: Optional[str],
    output: Optional[str],
    to_stdout: bool,
    skip_regulators: bool,
):
    """Generate a bare-metal C header with all peripheral base addresses and IRQs.

    Traverses every device node in DTS_FILE and emits ``#define`` macros for:

    \b
      • Peripheral base addresses  (FOO_BASE  0xFE2B0000UL)
      • IO sizes                   (FOO_SIZE  0x00010000UL)
      • IRQ numbers                (FOO_IRQ   34)
      • Clock IDs                  (FOO_CLK_HZ  200000000UL)
      • Regulator voltage ranges   (VCC_3V3_MIN_UV  3300000)

    The output compiles with any C89/C99/C11 cross-compiler.  Drop it into
    your bare-metal or RTOS project and ``#include`` it.

    \b
    Examples:
        socc export-headers board.dts > soc_hw.h
        socc export-headers board.dts -o include/soc_hw.h
        socc export-headers board.dts --stdout | grep I2C
    """
    from socc.codegen.headers import generate_headers

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    header = generate_headers(
        model,
        dts_name=Path(dts_file).name,
        include_regulators=not skip_regulators,
    )

    if to_stdout:
        click.echo(header, nl=False)
    else:
        out_path = output or (Path(dts_file).stem + "_hw.h")
        Path(out_path).write_text(header, encoding="utf-8")
        click.echo(f"C header written to: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# generate-diagram — Architecture diagram generator
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("generate-diagram")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["mermaid", "plantuml", "dot"]),
    default="mermaid",
    show_default=True,
    help="Diagram output format.",
)
@click.option(
    "--type", "diagram_type",
    type=click.Choice(["power", "clock", "bus", "full"]),
    default="full",
    show_default=True,
    help="Which topology to render.",
)
@click.option(
    "--output", "-o", default=None, metavar="FILE",
    help="Write diagram to FILE (default: stdout).",
)
def generate_diagram(
    dts_file: str,
    soc: Optional[str],
    fmt: str,
    diagram_type: str,
    output: Optional[str],
):
    """Generate a hardware topology diagram (Mermaid, PlantUML, or DOT).

    Translates the power tree, clock tree, and bus topology from DTS_FILE
    into a text-based diagram language that can be embedded in:

    \b
      • GitHub / GitLab README  (mermaid — rendered automatically)
      • Obsidian / Notion       (mermaid)
      • Lab reports / Word      (paste mermaid code block, render online)
      • Graphviz SVG export     (dot)
      • PlantUML PNG/SVG        (plantuml)

    \b
    Examples:
        socc generate-diagram board.dts --format mermaid >> README.md
        socc generate-diagram board.dts --format dot -o topology.dot
        dot -Tsvg topology.dot -o topology.svg
    """
    from socc.diagram import generate_diagram as _gen_diagram

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    text = _gen_diagram(model, fmt=fmt, diagram_type=diagram_type)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Diagram written to: {output}")
    else:
        click.echo(text)


# ─────────────────────────────────────────────────────────────────────────────
# explain — Datasheet reference mapper
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("explain")
@click.argument("node_or_name")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option(
    "--soc",
    type=click.Choice(_ALL_SOC_CHOICES),
    default=None,
    help="Target SoC (default: auto-detect from filename).",
)
@click.option(
    "--list", "list_kb", is_flag=True, default=False,
    help="List all documented hardware blocks in the knowledge base.",
)
def explain(
    node_or_name: str,
    dts_file: Optional[str],
    soc: Optional[str],
    list_kb: bool,
):
    """Explain a DTS node — hardware block, clocks, IRQ, and datasheet location.

    Acts as a 'senior engineer in your pocket': given a device path or name
    it tells you what the hardware block does, what each clock signal means,
    what the interrupt fires on, common pitfalls, and *exactly which chapter
    of the TRM* to read.

    \b
    Examples:
        socc explain /soc/i2c@fe580000 board.dts
        socc explain uart board.dts
        socc explain --list
        socc explain pmic board.dts --soc rk3588

    \b
    Node can be:
        • Full DTS path:    /soc/i2c@fe580000
        • Device name:      i2c5
        • Keyword:          pmic, uart, gpio, pcie, emmc ...
    """
    from socc.explain import explain_node, render_explain, list_knowledge_base

    if list_kb:
        click.echo(list_knowledge_base())
        return

    if dts_file is None:
        click.echo("Error: DTS_FILE is required unless --list is used.", err=True)
        raise SystemExit(1)

    soc_name = soc or _auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    result = explain_node(node_or_name, model)
    if result is None:
        click.echo(
            f"No node matching {node_or_name!r} found in {dts_file}.\n"
            "Try 'socc explain --list' to see documented blocks, "
            "or use a partial name like 'i2c', 'uart', 'pmic'.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(render_explain(result))


# ─────────────────────────────────────────────────────────────────────────────
# live-probe
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("live-probe")
@click.argument("dts_file")
@click.option("--svd", required=True, help="CMSIS-SVD XML file for the SoC")
@click.option("--simulate/--no-simulate", default=True, show_default=True,
              help="Use boot-default register values instead of live JTAG")
@click.option("--jtag-host", default="127.0.0.1", show_default=True,
              help="OpenOCD telnet host (only used without --simulate)")
@click.option("--jtag-port", default=4444, show_default=True, type=int,
              help="OpenOCD telnet port")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None, help="Write report to file")
def live_probe(
    dts_file: str,
    svd: str,
    simulate: bool,
    jtag_host: str,
    jtag_port: int,
    color: bool,
    output: Optional[str],
):
    """Silicon Lie Detector: compare DTS expectations vs physical register state.

    In --simulate mode (default) the tool uses SVD reset values to model the
    "driver never ran" scenario — no hardware needed.  Without --simulate it
    connects to a running OpenOCD instance via its telnet interface.
    """
    from socc.live_probe import run_live_probe, render_probe_report
    soc = parse_dts_file(dts_file)
    report = run_live_probe(
        soc, svd_path=svd, dts_path=dts_file,
        simulate=simulate,
        openocd_host=jtag_host, openocd_port=jtag_port,
    )
    text = render_probe_report(report, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Report written to {output}")
    else:
        click.echo(text)
    if report.mismatch_count > 0:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# matrix-audit
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("matrix-audit")
@click.argument("boards_dir")
@click.option("--change-type", default=None,
              type=click.Choice(["clock", "supply", "device"]),
              help="Type of proposed change to simulate")
@click.option("--change-name", default=None, help="Entity name (e.g. pll_audio)")
@click.option("--change-from", default=None, help="Old value (e.g. 1.0GHz)")
@click.option("--change-to",   default=None, help="New value (e.g. 1.2GHz)")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def matrix_audit(
    boards_dir: str,
    change_type: Optional[str],
    change_name: Optional[str],
    change_from: Optional[str],
    change_to:   Optional[str],
    color: bool,
    output: Optional[str],
):
    """Multi-SKU supply-chain variant matrix audit.

    Reads every .dts in BOARDS_DIR, builds the variant matrix, and reports
    cross-SKU clock / voltage / device divergence risks.

    To simulate a proposed change:
      socc matrix-audit boards/ --change-type clock
        --change-name pll_audio --change-from 1.0GHz --change-to 1.2GHz
    """
    from socc.matrix_audit import run_matrix_audit, PropagatedChange, render_matrix_report

    change = None
    if change_type and change_name and change_to:
        change = PropagatedChange(
            entity_type=change_type,
            entity_name=change_name,
            old_value=change_from or "?",
            new_value=change_to,
        )

    matrix = run_matrix_audit(boards_dir, proposed_change=change)
    text = render_matrix_report(matrix, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Report written to {output}")
    else:
        click.echo(text)
    if matrix.fatal_count > 0:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# generate-compliance
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("generate-compliance")
@click.argument("dts_file")
@click.option("--standard", default="iso26262-asil-b", show_default=True,
              type=click.Choice([
                  "iso26262-asil-a", "iso26262-asil-b",
                  "iso26262-asil-c", "iso26262-asil-d",
                  "iec61508-sil1", "iec61508-sil2",
                  "iec61508-sil3", "iec61508-sil4",
              ]),
              help="Safety standard and integrity level")
@click.option("--format", "output_format", default="text",
              type=click.Choice(["text", "markdown"]))
@click.option("--soc", default="auto")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def generate_compliance(
    dts_file: str,
    standard: str,
    output_format: str,
    soc: Optional[str],
    color: bool,
    output: Optional[str],
):
    """Generate an ISO 26262 / IEC 61508 functional-safety isolation report.

    Annotate safety-critical DTS nodes with:

      socc,safety-asil = "B";

    or:

      secure-status = "okay";

    The engine then traces power chains, clock chains, interrupt banks, and
    GPIO banks to verify each safety island is fully isolated.
    """
    from socc.compliance import generate_compliance_report, render_compliance_markdown, render_compliance_text
    soc_model = parse_dts_file(dts_file)
    report = generate_compliance_report(soc_model, dts_path=dts_file, standard_id=standard)

    if output_format == "markdown":
        text = render_compliance_markdown(report)
    else:
        text = render_compliance_text(report, use_color=color and output is None)

    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Compliance report written to {output}")
    else:
        click.echo(text)
    if not report.overall_pass and report.safety_nodes:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# crosscheck
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("crosscheck")
@click.argument("dts_file")
@click.argument("netlist_file")
@click.option("--format", "netlist_format", default=None,
              type=click.Choice(["kicad", "csv", "auto"]),
              help="Netlist format (default: auto-detect)")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def crosscheck(
    dts_file: str,
    netlist_file: str,
    netlist_format: Optional[str],
    color: bool,
    output: Optional[str],
):
    """Cross-check DTS pinctrl assignments against an EDA PCB netlist.

    Detects fatal mismatches such as:
      - Functional signal (I2C SDA) wired to GND on the PCB
      - 3.3V supply connected to a 1.8V net
      - DTS claims a different GPIO than the PCB routes

    Supported netlist formats: KiCad .net (Orcad), CSV pin→net tables.
    """
    from socc.netlist_crosscheck import parse_netlist, crosscheck_netlist_vs_dts, render_crosscheck_report
    soc_model = parse_dts_file(dts_file)
    netlist = parse_netlist(netlist_file, fmt=netlist_format)
    results = crosscheck_netlist_vs_dts(soc_model, netlist)
    text = render_crosscheck_report(
        results, netlist_path=netlist_file, dts_path=dts_file,
        use_color=color and output is None,
    )
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Crosscheck report written to {output}")
    else:
        click.echo(text)
    fatals = sum(1 for r in results if r.severity == "FATAL")
    if fatals > 0:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# socdef commands
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("validate-socdef")
@click.argument("socdef_file")
@click.option("--color/--no-color", default=True)
def validate_socdef(socdef_file: str, color: bool):
    """Validate a .socdef hardware constraint definition file.

    .socdef files are YAML/JSON files contributed by chip vendors that
    describe voltage domains, clock limits, pinmux exclusions, and
    compatible-string → kernel-config mappings for a specific SoC.
    """
    from socc.socdef import parse_socdef, validate_socdef as _validate
    soc_def = parse_socdef(socdef_file)
    errors = _validate(soc_def)
    _C = ("\033[1;31m", "\033[1;33m", "\033[1;32m", "\033[0m") if color else ("", "", "", "")
    if not errors:
        click.echo(f"{_C[2]}[✓] {socdef_file} is valid ({soc_def.soc} v{soc_def.version}){_C[3]}")
    else:
        for e in errors:
            col = _C[0] if e.severity == "error" else _C[1]
            click.echo(f"{col}[{e.severity.upper()}] {e.field}: {e.message}{_C[3]}")
        raise SystemExit(1)


@cli.command("check-socdef")
@click.argument("dts_file")
@click.option("--socdef", required=True, help=".socdef constraint file to apply")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def check_socdef(dts_file: str, socdef: str, color: bool, output: Optional[str]):
    """Check a DTS file against a vendor .socdef constraint file.

    This allows chip vendors to ship authoritative hardware constraint rules
    as data files rather than Python code.  Any .socdef file can be applied
    to any supported DTS without recompiling socc.
    """
    from socc.socdef import parse_socdef, check_dts_against_socdef, render_socdef_violations
    soc_def = parse_socdef(socdef)
    soc_model = parse_dts_file(dts_file)
    violations = check_dts_against_socdef(soc_model, soc_def)
    text = render_socdef_violations(violations, soc_def, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Written to {output}")
    else:
        click.echo(text)
    fatals = sum(1 for v in violations if v.severity in ("FATAL", "CRITICAL"))
    if fatals > 0:
        raise SystemExit(1)


@cli.command("init-socdef")
@click.option("--soc", required=True, help="SoC name (e.g. rk3588, imx8mp)")
@click.option("--vendor", default="unknown", show_default=True)
@click.option("-o", "--output", default=None)
def init_socdef(soc: str, vendor: str, output: Optional[str]):
    """Generate a .socdef template file for a new SoC.

    Fill in the template and submit it to the SoC-Consistency repository so
    all users of that SoC benefit from the hardware constraints.
    """
    from socc.socdef import generate_socdef_template
    tmpl = generate_socdef_template(soc, vendor)
    out = output or f"{soc}.socdef"
    Path(out).write_text(tmpl)
    click.echo(f"[INFO] Template written to {out}")
    click.echo(f"       Edit it and run: socc validate-socdef {out}")


# ─────────────────────────────────────────────────────────────────────────────
# auto-fix
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("autofix")
@click.argument("dts_file")
@click.option("--apply/--no-apply", default=False,
              help="Apply fixes to the file in-place (default: preview only)")
@click.option("--generate-patch", is_flag=True,
              help="Write a .patch file (does NOT modify the DTS)")
@click.option("-o", "--output", default=None, help="Patch output path (default: auto-named)")
@click.option("--color/--no-color", default=True)
def autofix(
    dts_file: str,
    apply: bool,
    generate_patch: bool,
    output: Optional[str],
    color: bool,
):
    """Auto-fix common DTS hardware constraint violations.

    Detects and repairs:
      - status property typos  (okau → okay)
      - Voltage domain mismatches in supply properties
      - Clock overspeed in assigned-clock-rates
      - Duplicate compatible strings

    Preview only (default):
      socc autofix board.dts

    Apply in-place:
      socc autofix board.dts --apply

    Generate a git-apply-ready patch:
      socc autofix board.dts --generate-patch -o 0001-socc-fix.patch
    """
    from socc.autofix import generate_fixes_from_dts, apply_fixes_to_file, render_patch_summary

    patch_set = generate_fixes_from_dts(dts_file)
    click.echo(render_patch_summary(patch_set, use_color=color))

    if not patch_set.fixes:
        return

    if generate_patch:
        patch_text = patch_set.as_unified_patch()
        out = output or f"0001-socc-autofix-{Path(dts_file).stem}.patch"
        Path(out).write_text(patch_text)
        click.echo(f"\n[SUCCESS] Patch written to {out}")
        click.echo(f"          Apply with: git apply {out}")
    elif apply:
        n = apply_fixes_to_file(dts_file, patch_set.fixes)
        click.echo(f"\n[SUCCESS] Applied {n} fix(es) to {dts_file}")
    else:
        click.echo("\n[INFO] Preview only. Use --apply or --generate-patch to modify the file.")


# ─────────────────────────────────────────────────────────────────────────────
# audit-kernel
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("audit-kernel")
@click.argument("dts_file")
@click.option("--config", required=True,
              help="Path to the Linux kernel .config file")
@click.option("--compat-db", default=None,
              help="Extra YAML/JSON file with compatible → CONFIG mappings")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def audit_kernel(
    dts_file: str,
    config: str,
    compat_db: Optional[str],
    color: bool,
    output: Optional[str],
):
    """Cross-check DTS-enabled devices against a Linux kernel .config.

    Finds devices that are powered on in the DTS but whose kernel driver is
    NOT compiled in — the hardware will be energised but the OS can never
    talk to it.

    Example:

      socc audit-kernel board.dts --config /path/to/linux/.config

    Output:

      [KERNEL MISMATCH ERROR]
      Hardware Node : /soc/gpu@fe280000
      Compatible    : rockchip,rk3588-mali
      Kernel Config : CONFIG_DRM_PANFROST = NOT SET
      Impact        : GPU is powered on but Linux has no driver for it.
      Fix           : Enable CONFIG_DRM_PANFROST in .config
    """
    from socc.kernel_audit import run_kernel_audit, render_kernel_audit
    soc_model = parse_dts_file(dts_file)
    report = run_kernel_audit(soc_model, config_path=config, dts_path=dts_file,
                              extra_db_path=compat_db)
    text = render_kernel_audit(report, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Report written to {output}")
    else:
        click.echo(text)
    if report.error_count > 0:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# generate-report (socc-ui)
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("generate-report")
@click.argument("dts_file")
@click.option("--format", "output_format", default="html",
              type=click.Choice(["html"]),
              help="Output format (currently: html)")
@click.option("--soc", default="auto")
@click.option("-o", "--output", default=None,
              help="Output file (default: <stem>-report.html)")
def generate_report(
    dts_file: str,
    output_format: str,
    soc: Optional[str],
    output: Optional[str],
):
    """Generate a self-contained HTML architecture report.

    Produces a single-file HTML dashboard with:
      - Peripheral register map table
      - Power domain summary
      - Clock tree (top frequencies)
      - Embedded Mermaid diagrams
      - Full socc check violations (colour-coded)

    Open the output in any browser — no server needed.

      socc generate-report board.dts -o report.html
    """
    from socc.report import generate_html_report
    from socc.engine import Checker

    soc_model = parse_dts_file(dts_file)

    # Run socc check and collect violations as dicts for the report
    registry = _build_registry()
    detected_soc = soc if soc != "auto" else _auto_detect_soc(Path(dts_file).name)
    checker = Checker(registry, soc_name=detected_soc)
    raw_violations = checker.check(soc_model)
    violations = [
        {
            "severity": v.severity,
            "node_path": v.node_path,
            "description": v.message,
            "fix": getattr(v, "suggestion", ""),
        }
        for v in raw_violations
    ]

    html_text = generate_html_report(soc_model, dts_path=dts_file, violations=violations)
    out = output or f"{Path(dts_file).stem}-report.html"
    Path(out).write_text(html_text)
    click.echo(f"[INFO] HTML report written to {out}")
    click.echo(f"       Open in browser: file://{Path(out).resolve()}")


# ─────────────────────────────────────────────────────────────────────────────
# check-memory  —  MMIO region overlap & sanity scanner
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("check-memory")
@click.argument("dts_file")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def check_memory(dts_file: str, color: bool, output: Optional[str]):
    """Scan MMIO address map for overlapping or invalid regions.

    Extracts every ``reg`` property from the device tree, converts each entry
    to a half-open interval [base, base+size), and runs a sweep-line algorithm
    to detect overlaps.

    \b
    Error classes:
      MM-001  Identical window (100 % overlap — duplicate node)
      MM-002  Full containment (one region inside another)
      MM-003  Partial overlap  (ioremap will corrupt both drivers)
      MM-004  Zero-size region (driver cannot map registers)
      MM-005  Suspiciously large region (likely a missing zero)

    \b
    Example output:
      [ERROR] MM-003  Partial overlap of 0x800 bytes:
        Node A: /soc/i2c@fe2b0000  [0xfe2b0000–0xfe2b1000]
        Node B: /soc/spi@fe2b0800  [0xfe2b0800–0xfe2b0a00]
        → ioremap() of these two drivers will produce silent data corruption.
    """
    from socc.memmap import check_memory as _check_memory, render_memmap_report
    soc_model = parse_dts_file(dts_file)
    report = _check_memory(soc_model)
    text = render_memmap_report(report, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Memory map report written to {output}")
    else:
        click.echo(text)
    if not report.pass_result:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# check-deps  —  power / clock dependency graph cycle detector
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("check-deps")
@click.argument("dts_file")
@click.option("--fan-out-limit", default=16, show_default=True, type=int,
              help="Warn when a single rail/clock drives more than N consumers")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def check_deps(
    dts_file: str,
    fan_out_limit: int,
    color: bool,
    output: Optional[str],
):
    """Detect dependency cycles and orphan nodes in the power/clock graph.

    Constructs a directed graph from all ``xxx-supply`` and ``clocks``
    references, then runs:

    \b
      1. DFS cycle detection  — finds boot-time deadlocks in the regulator /
         clk framework before the kernel ever runs user-space.
      2. Orphan reference check  — finds supply/clock names that are
         referenced but never defined in the DTS (causes -EPROBE_DEFER loops).
      3. Fan-out anomaly  — warns when a single rail powers too many
         consumers (inrush current / sequencing race risk).

    \b
    Example output:
      [FATAL] DG-CP01  Power supply dependency cycle:
        Path: vcc_1v8_s0 → vcc_3v3_sys → vcc_5v0_sys → vcc_1v8_s0
        → The kernel regulator framework will deadlock at boot.
    """
    from socc.depgraph import check_deps as _check_deps, render_dep_report
    soc_model = parse_dts_file(dts_file)
    report = _check_deps(soc_model, fan_out_limit=fan_out_limit)
    text = render_dep_report(report, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Dependency report written to {output}")
    else:
        click.echo(text)
    if not report.pass_result:
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# smart-diff  —  semantic DTB/DTS comparison
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("smart-diff")
@click.argument("file_a")
@click.argument("file_b")
@click.option("--format", "output_format", default="text",
              type=click.Choice(["text", "markdown", "json"]))
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def smart_diff(
    file_a: str,
    file_b: str,
    output_format: str,
    color: bool,
    output: Optional[str],
):
    """Semantic diff of two device tree files (.dts or .dtb).

    Strips labels, comments, phandle values, and node ordering — comparing
    only the hardware-relevant semantic content.  Accepts any mix of binary
    .dtb and source .dts files.

    \b
    Requires ``dtc`` on PATH to decompile .dtb inputs:
      apt install device-tree-compiler   (Debian/Ubuntu)
      brew install dtc                   (macOS)

    \b
    Example output:
      /soc/pcie@fe150000
        [~] max-link-speed
            A: 2 (PCIe Gen2 — 5 GT/s)
            B: 3 (PCIe Gen3 — 8 GT/s)
            💡 The vendor board likely has stability issues at Gen3 speeds.
    """
    from socc.smartdiff import (
        smart_diff as _smart_diff,
        render_diff_text, render_diff_markdown, render_diff_json,
        DTBDecodeError,
    )
    try:
        report = _smart_diff(file_a, file_b)
    except DTBDecodeError as e:
        click.echo(f"[ERROR] {e}", err=True)
        raise SystemExit(1)

    if output_format == "markdown":
        text = render_diff_markdown(report)
    elif output_format == "json":
        text = render_diff_json(report)
    else:
        text = render_diff_text(report, use_color=color and output is None)

    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Diff report written to {output}")
    else:
        click.echo(text)

    if report.total > 0:
        raise SystemExit(1)


# ── gc  —  DTS zombie-node garbage collector ──────────────────────────────────

@cli.command("gc")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default=None, help="SoC name (e.g. rk3588)")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]),
              show_default=True, help="Output format.")
@click.option("--threshold", default=1, show_default=True,
              help="Minimum zombie count before non-zero exit.")
@click.option("-o", "--output", default=None, help="Write report to FILE.")
def gc_cmd(dts_file: str, soc: str, fmt: str, threshold: int, output: str) -> None:
    """Find and report unreferenced (zombie) DTS nodes.

    Parses DTS_FILE and identifies nodes that are disabled AND not
    transitively referenced by any enabled node.  These are safe to
    delete to reduce DTB size and kernel boot time.

    Exits with code 1 when the zombie count >= THRESHOLD.
    """
    from socc.gc import run_gc, render_gc_text, render_gc_json

    soc_name = soc or "generic"
    model = parse_dts_file(dts_file, soc_name)

    report = run_gc(model)

    if fmt == "json":
        text = render_gc_json(report)
    else:
        text = render_gc_text(report, use_color=True)

    if output:
        from pathlib import Path
        Path(output).write_text(text)
        click.echo(f"[INFO] GC report written to {output}")
    else:
        click.echo(text)

    if report.zombie_count >= threshold:
        raise SystemExit(1)


# ── check-bounds  —  physical hardware bounds auditor ────────────────────────

@cli.command("check-bounds")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default="rk3588", show_default=True,
              help="Target SoC name (used to look up hardware limits).")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]),
              show_default=True, help="Output format.")
@click.option("-o", "--output", default=None, help="Write report to FILE.")
def check_bounds_cmd(dts_file: str, soc: str, fmt: str, output: str) -> None:
    """Detect copy-paste hardware bounds violations.

    Checks GPIO pin indices, DMA channel numbers, and PWM channel
    indices against the physical limits of the target SoC.  Flags any
    property that references a resource beyond what the silicon
    provides (e.g. gpio1 pin 35 on an RK3588 GPIO bank with only 32
    pins).

    Exits with code 1 when any FATAL violation is found.
    """
    from socc.bounds import check_bounds, render_bounds_text, render_bounds_json

    model = parse_dts_file(dts_file, soc)

    report = check_bounds(model)

    if fmt == "json":
        text = render_bounds_json(report)
    else:
        text = render_bounds_text(report, use_color=True)

    if output:
        from pathlib import Path
        Path(output).write_text(text)
        click.echo(f"[INFO] Bounds report written to {output}")
    else:
        click.echo(text)

    if not report.pass_result:
        raise SystemExit(1)


# ── check-irq  —  IRQ collision & routing checker ────────────────────────────

@cli.command("check-irq")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default=None, help="SoC name (informational).")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]),
              show_default=True, help="Output format.")
@click.option("-o", "--output", default=None, help="Write report to FILE.")
def check_irq_cmd(dts_file: str, soc: str, fmt: str, output: str) -> None:
    """Detect IRQ collisions, reserved-PPI use, and routing mismatches.

    Parses all 'interrupts' properties and builds a global interrupt
    allocation table.  Reports:

    \b
      IRQ-C01  Two active nodes share the same non-shared interrupt line
      IRQ-C02  Device driver bound to architecturally reserved PPI
      IRQ-C03  interrupt-parent points to missing or disabled controller
      IRQ-C04  Interrupt controller missing '#interrupt-cells' property

    Exits with code 1 when any CRITICAL issue is found.
    """
    from socc.irqcheck import check_irq, render_irq_text, render_irq_json

    soc_name = soc or "generic"
    model = parse_dts_file(dts_file, soc_name)

    report = check_irq(model)

    if fmt == "json":
        text = render_irq_json(report)
    else:
        text = render_irq_text(report, use_color=True)

    if output:
        from pathlib import Path
        Path(output).write_text(text)
        click.echo(f"[INFO] IRQ report written to {output}")
    else:
        click.echo(text)

    if not report.pass_result:
        raise SystemExit(1)


def main():
    """CLI main entry point."""
    cli()


if __name__ == "__main__":
    main()

