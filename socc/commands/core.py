"""Top-level socc commands: check, fix, autofix, diff, smart-diff,
explain, version, rules, init, self-update."""

from __future__ import annotations

import json as _json
import sys

import click

from socc import __version__
from socc.commands._shared import (
    ALL_SOC_CHOICES, build_registry, auto_detect_soc, severity_tag, echo,
    load_config, filter_by_severity, SAMPLE_CONFIG,
    build_sample_model, parse_dts_file, Checker, Path, Optional,
)


# ── check ──────────────────────────────────────────────────────────────────

@click.command()
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None,
              help="Target SoC name (use 'auto' for filename-based auto-detection).")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "sarif"]),
              default=None, help="Output format (text, json, or sarif for GitHub Code Scanning).")
@click.option("--min-severity", type=click.Choice(["error", "warning", "info"]),
              default=None, help="Minimum severity level to report.")
@click.option("--ignore-rule", multiple=True, metavar="CODE",
              help="Suppress a rule code (can be repeated).")
@click.option("--skip-rules", multiple=True, hidden=True,
              help="[Deprecated] Use --ignore-rule instead.")
@click.option("--color/--no-color", default=None,
              help="Force colored output on or off (default: auto-detect TTY).")
@click.option("--demo", is_flag=True,
              help="Run in demo mode using built-in sample data.")
@click.option("--netlist", "netlist_csv", type=click.Path(exists=True), default=None,
              metavar="CSV",
              help="Path to an EDA pin-assignment CSV for netlist cross-checking.")
def check(dts_file, soc, output_format, min_severity, ignore_rule,
          skip_rules, color, demo, netlist_csv):
    """Check a device tree for SoC consistency violations."""
    cfg = load_config()
    resolved_soc = soc or cfg.get("default_soc", "auto")
    resolved_format = output_format or cfg.get("format", "text")
    resolved_min_severity = min_severity or cfg.get("min_severity", "info")
    ignored = set(cfg.get("ignore_rules", [])) | set(ignore_rule) | set(skip_rules)
    use_color = color if color is not None else None

    registry = build_registry()

    if demo:
        echo("Running in demo mode...", color=use_color)
        model = build_sample_model("rk3588")
        soc_name = "rk3588"
    else:
        if not dts_file:
            click.echo("Error: specify a device-tree file or use --demo", err=True)
            return
        try:
            echo(f"Loading device tree: {dts_file}", color=use_color)
            if resolved_soc == "auto":
                soc_name = auto_detect_soc(Path(dts_file).name)
            else:
                soc_name = resolved_soc
            model = parse_dts_file(dts_file, soc_name)
            echo(f"Loaded device tree (SoC: {soc_name})", color=use_color)
        except FileNotFoundError:
            click.echo(f"Error: file not found: {dts_file!r}", err=True)
            return
        except Exception as e:
            click.echo(f"Error: failed to parse device tree: {e}", err=True)
            return

    extra_metadata: dict = {}
    if netlist_csv:
        try:
            from socc.netlist.parser import parse_netlist_csv
            netlist_pins = parse_netlist_csv(netlist_csv)
            extra_metadata["netlist_pins"] = netlist_pins
            echo(f"Loaded netlist: {netlist_csv} ({len(netlist_pins)} pins)", color=use_color)
        except Exception as e:
            click.echo(f"Warning: could not load netlist CSV: {e}", err=True)

    checker = Checker(registry)
    violations = checker.check(model, soc_name, extra_metadata=extra_metadata)

    if ignored:
        violations = [v for v in violations if v.code not in ignored]
    violations = filter_by_severity(violations, resolved_min_severity)

    report = checker.generate_report(violations, resolved_format, color=use_color)
    click.echo(report)

    if any(v.severity == "error" for v in violations):
        raise SystemExit(1)


# ── rules ──────────────────────────────────────────────────────────────────

@click.command()
def rules():
    """List all available rules (all vendors)."""
    registry = build_registry()
    all_rules = sorted(registry.list_all_rules(), key=lambda r: r.code)
    use_color = None
    click.echo("=" * 100)
    click.echo("SoC-Consistency Available Rules")
    click.echo("=" * 100)
    click.echo()
    for rule in all_rules:
        sym = severity_tag(rule.severity, use_color)
        click.echo(f"{sym} [{rule.code}] {rule.name}")
        click.echo(f"  Description: {rule.description}")
        click.echo()


# ── version ─────────────────────────────────────────────────────────────────

@click.command()
def version():
    """Show version information."""
    click.echo(f"socc version {__version__}")
    click.echo("SoC-Consistency Device Tree Checker")
    click.echo("Supported vendors: Rockchip, Allwinner, Amlogic, Qualcomm, NXP")


# ── fix ─────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None,
              help="Target SoC (default: auto-detect from filename).")
@click.option("--color/--no-color", default=None,
              help="Force colored output on or off.")
def fix(dts_file: str, soc: Optional[str], color: Optional[bool]):
    """Suggest automated fixes for common DTS violations."""
    use_color = color
    registry = build_registry()
    soc_name = soc if (soc and soc != "auto") else auto_detect_soc(Path(dts_file).name)
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
        tag = severity_tag(v.severity, use_color)
        click.echo(f"{tag} [{v.code}] {v.message}")
        click.echo(head("  --- Suggested fix ---"))
        for line in v.suggestion.splitlines():
            click.echo(add(f"  + {line}"))
        click.echo()


# ── autofix ─────────────────────────────────────────────────────────────────

@click.command()
@click.argument("dts_file")
@click.option("--apply/--no-apply", default=False,
              help="Apply fixes to the file in-place (default: preview only)")
@click.option("--generate-patch", is_flag=True,
              help="Write a .patch file (does NOT modify the DTS)")
@click.option("-o", "--output", default=None, help="Patch output path (default: auto-named)")
@click.option("--color/--no-color", default=True)
def autofix(dts_file: str, apply: bool, generate_patch: bool,
            output: Optional[str], color: bool):
    """Auto-fix common DTS hardware constraint violations.

    \b
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


# ── diff ─────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("before_dts", type=click.Path(exists=True))
@click.argument("after_dts", type=click.Path(exists=True))
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None,
              help="Target SoC (default: auto-detect from filename).")
@click.option("--color/--no-color", default=None,
              help="Force colored output on or off.")
@click.option("--format", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format.")
def diff(before_dts: str, after_dts: str, soc: Optional[str],
         color: Optional[bool], output_format: str):
    """Show violations introduced between two DTS versions (regressions only)."""
    use_color = color
    registry = build_registry()

    def _detect(path: str) -> str:
        if soc and soc != "auto":
            return soc
        return auto_detect_soc(Path(path).name)

    def _run(path: str, soc_name: str):
        try:
            model = parse_dts_file(path, soc_name)
        except Exception as e:
            click.echo(f"Error parsing {path}: {e}", err=True)
            raise SystemExit(1)
        checker = Checker(registry)
        return checker.check(model, soc_name)

    violations_before = _run(before_dts, _detect(before_dts))
    violations_after  = _run(after_dts,  _detect(after_dts))

    before_keys = {(v.code, v.message) for v in violations_before}
    regressions = [v for v in violations_after if (v.code, v.message) not in before_keys]

    if output_format == "json":
        data = [{"code": v.code, "severity": v.severity, "message": v.message,
                 "location": v.location, "suggestion": v.suggestion} for v in regressions]
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
        tag = severity_tag(v.severity, use_color)
        loc = f"  at {v.location}" if v.location else ""
        click.echo(f"  {tag} [{v.code}] {v.message}{loc}")
        if v.suggestion:
            click.echo(f"       Fix: {v.suggestion.splitlines()[0]}")
        click.echo()

    if any(v.severity == "error" for v in regressions):
        raise SystemExit(1)


# ── smart-diff ───────────────────────────────────────────────────────────────

@click.command("smart-diff")
@click.argument("file_a")
@click.argument("file_b")
@click.option("--format", "output_format", default="text",
              type=click.Choice(["text", "markdown", "json"]))
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def smart_diff(file_a: str, file_b: str, output_format: str,
               color: bool, output: Optional[str]):
    """Semantic diff of two device tree files (.dts or .dtb).

    Strips labels, comments, phandle values, and node ordering — comparing
    only the hardware-relevant semantic content.
    """
    from socc.smartdiff import smart_diff as _smart_diff
    from socc.smartdiff import render_diff_text, render_diff_markdown, render_diff_json, DTBDecodeError
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


# ── explain ──────────────────────────────────────────────────────────────────

@click.command()
@click.argument("node_or_name")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None,
              help="Target SoC (default: auto-detect from filename).")
@click.option("--list", "list_kb", is_flag=True, default=False,
              help="List all documented hardware blocks in the knowledge base.")
def explain(node_or_name: str, dts_file: Optional[str],
            soc: Optional[str], list_kb: bool):
    """Explain a DTS node — hardware block, clocks, IRQ, and datasheet location."""
    from socc.explain import explain_node, render_explain, list_knowledge_base
    if list_kb:
        click.echo(list_knowledge_base())
        return
    if dts_file is None:
        click.echo("Error: DTS_FILE is required unless --list is used.", err=True)
        raise SystemExit(1)
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    result = explain_node(node_or_name, model)
    if result is None:
        click.echo(
            f"No node matching {node_or_name!r} found in {dts_file}.\n"
            "Try 'socc explain --list' to see documented blocks.",
            err=True)
        raise SystemExit(1)
    click.echo(render_explain(result))


# ── init ─────────────────────────────────────────────────────────────────────

@click.command("init")
@click.option("--output", default=".socc.yaml", show_default=True,
              help="Destination path for the config file.")
@click.option("--force", is_flag=True, help="Overwrite an existing config file.")
def init_cmd(output: str, force: bool):
    """Create a .socc.yaml config file with commented defaults."""
    dest = Path(output)
    if dest.exists() and not force:
        click.echo(f"Error: {output} already exists. Use --force to overwrite.", err=True)
        raise SystemExit(1)
    dest.write_text(SAMPLE_CONFIG)
    click.echo(f"Created {output}")


# ── self-update ──────────────────────────────────────────────────────────────

@click.command("self-update")
@click.option("--check-only", is_flag=True,
              help="Only check for a newer version; do not install.")
def self_update(check_only: bool):
    """Upgrade socc to the latest version from PyPI."""
    import json, urllib.request, subprocess
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

    def _ver(v):
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0,)

    if _ver(latest) <= _ver(__version__):
        click.echo(click.style("Already up-to-date.", fg="green", bold=True))
        return
    click.echo(click.style(f"\nNew version available: {latest}", fg="yellow", bold=True))
    if check_only:
        click.echo("Run 'socc self-update' (without --check-only) to install.")
        return
    click.echo(f"Installing socc {latest} ...\n")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "soc-consistency"],
        check=False)
    if result.returncode == 0:
        click.echo(click.style(f"\nUpgraded to socc {latest} successfully.", fg="green", bold=True))
        click.echo("Restart the terminal for the new version to take effect.")
    else:
        click.echo("pip upgrade failed.  Try manually:", err=True)
        click.echo("  pip install --upgrade soc-consistency", err=True)
        raise SystemExit(1)
