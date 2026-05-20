"""socc audit <subcommand> — DTS auditing commands."""

from __future__ import annotations

import click

from socc.commands._shared import (
    ALL_SOC_CHOICES, FuzzySoCType, build_registry, auto_detect_soc,
    parse_dts_file, parse_dts_cached, Checker, Path, Optional,
)


@click.group("audit")
def audit_group():
    """Audit DTS compatibility, BOM, kernel config, AMP, and more."""


# ── audit bindings ────────────────────────────────────────────────────────────

@audit_group.command("bindings")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--target", type=click.Choice(["mainline"]), default="mainline",
              show_default=True, help="Target kernel tree to audit against.")
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None,
              help="Target SoC name.")
@click.option("--format", "output_format", type=click.Choice(["text", "json"]),
              default="text", show_default=True)
@click.option("--color/--no-color", default=None)
def audit_bindings(dts_file: str, target: str, soc: Optional[str],
                   output_format: str, color: Optional[bool]):
    """Audit DTS bindings for compatibility with a target kernel tree.

    \b
    Examples:
        socc audit bindings board.dts
        socc audit bindings board.dts --format json
    """
    import json as _json
    from socc.rules.common.compat_rules import COMP101DeprecatedVendorBinding
    from socc.rules.base import CheckContext

    use_color = color
    resolved_soc = soc or "auto"
    if resolved_soc == "auto":
        resolved_soc = auto_detect_soc(Path(dts_file).name)

    try:
        model = parse_dts_file(dts_file, resolved_soc)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    rule = COMP101DeprecatedVendorBinding()
    context = CheckContext(soc_name=resolved_soc)
    findings = rule.check(model, context)

    if output_format == "json":
        out = {
            "dts_file": str(dts_file), "target": target, "soc": resolved_soc,
            "findings": [{"code": f.code, "severity": f.severity, "message": f.message,
                          "impact": f.impact, "suggestion": f.suggestion,
                          "location": f.location, "affected_nodes": f.affected_nodes}
                         for f in findings],
        }
        click.echo(_json.dumps(out, indent=2))
        if findings:
            raise SystemExit(1)
        return

    if not findings:
        click.echo(click.style(f"\nNo deprecated vendor bindings found in {dts_file}",
                               fg="green", bold=True))
        return

    click.echo(click.style(f"\nVendor-to-Mainline Audit: {Path(dts_file).name}",
                           fg="cyan", bold=True))
    click.echo(f"  Target: mainline Linux  |  SoC: {resolved_soc}  |  Findings: {len(findings)}\n")

    for i, v in enumerate(findings, 1):
        sev_color = {"error": "red", "warning": "yellow", "info": "blue"}.get(v.severity, "white")
        click.echo(click.style(f"  [{i}] {v.code}", fg=sev_color, bold=True) + f"  {v.message}")
        click.echo(f"       Impact:     {v.impact}")
        click.echo(click.style(f"       Fix:        {v.suggestion}", fg="green"))
        if v.location:
            click.echo(f"       Location:   {v.location}")
        click.echo()

    raise SystemExit(1)


# ── audit bom ─────────────────────────────────────────────────────────────────

@audit_group.command("bom")
@click.argument("dts_file", type=click.Path(exists=True))
@click.argument("bom_csv", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]),
              default="text", show_default=True)
@click.option("--color/--no-color", default=None)
def audit_bom(dts_file: str, bom_csv: str, soc: Optional[str],
              output_format: str, color: Optional[bool]):
    """Cross-reference a hardware BOM against the device tree.

    \b
    Examples:
        socc audit bom board.dts hardware_bom.csv
        socc audit bom board.dts bom.csv --format json
    """
    import json as _json, dataclasses
    from socc.bom import parse_bom_csv, audit_bom as _audit_bom, render_bom_report

    soc_name = soc or auto_detect_soc(Path(dts_file).name)
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
        click.echo(_json.dumps([dataclasses.asdict(v) for v in violations], indent=2))
    else:
        click.echo(render_bom_report(violations, bom_csv, dts_file, use_color=color))

    if any(v.severity == "CRITICAL" for v in violations):
        raise SystemExit(1)


# ── audit kernel ─────────────────────────────────────────────────────────────

@audit_group.command("kernel")
@click.argument("dts_file")
@click.option("--config", required=True, help="Path to the Linux kernel .config file")
@click.option("--compat-db", default=None,
              help="Extra YAML/JSON file with compatible → CONFIG mappings")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def audit_kernel(dts_file: str, config: str, compat_db: Optional[str],
                 color: bool, output: Optional[str]):
    """Cross-check DTS-enabled devices against a Linux kernel .config.

    Finds devices that are powered on in the DTS but whose kernel driver is
    NOT compiled in.

    \b
    Example:
      socc audit kernel board.dts --config /path/to/linux/.config
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


# ── audit amp ─────────────────────────────────────────────────────────────────

@audit_group.command("amp")
@click.argument("linux_dts", type=click.Path(exists=True))
@click.argument("rtos_dts", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("--color/--no-color", default=None)
def audit_amp(linux_dts: str, rtos_dts: str, soc: Optional[str],
              color: Optional[bool]):
    """Detect resource conflicts in an AMP (Linux + RTOS) configuration.

    \b
    Examples:
        socc audit amp linux.dts zephyr.dts
        socc audit amp rock5b_linux.dts freertos_m0.dts --soc rk3588
    """
    from socc.amp import amp_audit as _amp_audit, render_amp_report
    soc_name = soc or auto_detect_soc(Path(linux_dts).name)
    try:
        linux_model = parse_dts_file(linux_dts, soc_name)
        rtos_model  = parse_dts_file(rtos_dts,  soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    conflicts = _amp_audit(linux_model, rtos_model)
    click.echo(render_amp_report(conflicts, linux_dts, rtos_dts, use_color=color))
    if any(c.severity == "FATAL" for c in conflicts):
        raise SystemExit(1)


# ── audit matrix ──────────────────────────────────────────────────────────────

@audit_group.command("matrix")
@click.argument("boards_dir")
@click.option("--change-type", default=None,
              type=click.Choice(["clock", "supply", "device"]))
@click.option("--change-name", default=None)
@click.option("--change-from", default=None)
@click.option("--change-to", default=None)
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def audit_matrix(boards_dir: str, change_type: Optional[str], change_name: Optional[str],
                 change_from: Optional[str], change_to: Optional[str],
                 color: bool, output: Optional[str]):
    """Multi-SKU supply-chain variant matrix audit.

    \b
    Example:
      socc audit matrix boards/ --change-type clock --change-name pll_audio
        --change-from 1.0GHz --change-to 1.2GHz
    """
    from socc.matrix_audit import run_matrix_audit, PropagatedChange, render_matrix_report
    change = None
    if change_type and change_name and change_to:
        change = PropagatedChange(entity_type=change_type, entity_name=change_name,
                                  old_value=change_from or "?", new_value=change_to)
    matrix = run_matrix_audit(boards_dir, proposed_change=change)
    text = render_matrix_report(matrix, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Report written to {output}")
    else:
        click.echo(text)
    if matrix.fatal_count > 0:
        raise SystemExit(1)


# ── audit cross-check ─────────────────────────────────────────────────────────

@audit_group.command("cross-check")
@click.argument("bootloader_dts", type=click.Path(exists=True))
@click.argument("kernel_dts", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default="auto", show_default=True)
@click.option("--report", type=click.Choice(["text", "html", "json"]),
              default="text", show_default=True)
@click.option("-o", "--output", default=None, metavar="FILE")
@click.option("--color/--no-color", default=None)
def audit_cross_check(bootloader_dts: str, kernel_dts: str, soc: str,
                      report: str, output: Optional[str], color: Optional[bool]):
    """Cross-validate Bootloader vs Linux DTS for critical node divergence."""
    from socc.crosscheck.comparator import compare_dts_stages, format_report
    soc_name = auto_detect_soc(Path(kernel_dts).name) if soc == "auto" else soc
    try:
        bl_model = parse_dts_file(bootloader_dts, soc_name)
        kn_model = parse_dts_file(kernel_dts, soc_name)
    except Exception as e:
        click.echo(f"Error parsing DTS: {e}", err=True)
        raise SystemExit(1)
    diffs = compare_dts_stages(bl_model, kn_model)
    result_text = format_report(diffs, fmt=report, use_color=color,
                                bootloader_path=bootloader_dts,
                                kernel_path=kernel_dts, soc_name=soc_name)
    if output:
        Path(output).write_text(result_text, encoding="utf-8")
        click.echo(f"Report written to: {output}")
    else:
        click.echo(result_text)
    if any(d.severity == "error" for d in diffs):
        raise SystemExit(1)


# ── audit overlay ─────────────────────────────────────────────────────────────

@audit_group.command("overlay")
@click.argument("base_dts", type=click.Path(exists=True))
@click.argument("overlays", type=click.Path(exists=True), nargs=-1, required=True)
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default="auto", show_default=True)
@click.option("--run-checks", is_flag=True,
              help="Also run consistency rules on the merged tree.")
@click.option("--color/--no-color", default=None)
def audit_overlay(base_dts: str, overlays: tuple, soc: str,
                  run_checks: bool, color: Optional[bool]):
    """Simulate merging Device Tree Overlays and detect conflicts.

    \b
    Example:
      socc audit overlay board.dts camera.dtbo display.dtbo --run-checks
    """
    from socc.overlay.merger import OverlayMerger
    use_color = color if color is not None else None
    soc_name = auto_detect_soc(Path(base_dts).name) if soc == "auto" else soc

    merger = OverlayMerger(base_dts)
    for ov in overlays:
        merger.add_overlay(ov)
    conflicts = merger.detect_conflicts()
    click.echo(merger.report(use_color=use_color is not False))

    if run_checks:
        click.echo("\nRunning consistency rules on merged tree ...")
        try:
            model = merger.merged_model(soc_name=soc_name)
            checker = Checker(build_registry())
            violations = checker.check(model, soc_name)
            if violations:
                for v in violations:
                    tag = "[E]" if v.severity == "error" else "[W]"
                    click.echo(f"  {tag} [{v.code}] {v.message[:100]}")
            else:
                click.echo("  No rule violations on merged tree.")
        except Exception as e:
            click.echo(f"  Warning: could not run checks: {e}", err=True)

    if any(c.severity == "error" for c in conflicts):
        raise SystemExit(1)


# ── audit netlist ─────────────────────────────────────────────────────────────

@audit_group.command("netlist")
@click.argument("dts_file")
@click.argument("netlist_file")
@click.option("--format", "netlist_format", default=None,
              type=click.Choice(["kicad", "csv", "auto"]))
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def audit_netlist(dts_file: str, netlist_file: str, netlist_format: Optional[str],
                  color: bool, output: Optional[str]):
    """Cross-check DTS pinctrl assignments against an EDA PCB netlist."""
    from socc.netlist_crosscheck import parse_netlist, crosscheck_netlist_vs_dts, render_crosscheck_report
    soc_model = parse_dts_file(dts_file)
    netlist = parse_netlist(netlist_file, fmt=netlist_format)
    results = crosscheck_netlist_vs_dts(soc_model, netlist)
    text = render_crosscheck_report(results, netlist_path=netlist_file, dts_path=dts_file,
                                    use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Crosscheck report written to {output}")
    else:
        click.echo(text)
    if sum(1 for r in results if r.severity == "FATAL") > 0:
        raise SystemExit(1)


# ── audit sku ─────────────────────────────────────────────────────────────────

@audit_group.command("sku")
@click.argument("dts_files", nargs=-1, required=True,
                type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("--format", "output_format",
              type=click.Choice(["table", "json"]),
              default="table", show_default=True)
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None, metavar="FILE")
def audit_sku(dts_files: tuple, soc: Optional[str], output_format: str,
              color: bool, output: Optional[str]):
    """Compare multiple DTS SKU variants side-by-side.

    Loads two or more DTS files (board variants that share the same SoC)
    and produces a three-column diff showing which nodes/properties differ
    across SKUs — useful for verifying product variant consistency before
    release.

    \b
    Examples:
        socc audit sku rock5b.dts rock5b-plus.dts
        socc audit sku sku-a.dts sku-b.dts sku-c.dts --format json
    """
    import json as _json

    if len(dts_files) < 2:
        click.echo("Error: provide at least two DTS files to compare.", err=True)
        raise SystemExit(1)

    soc_name = soc or auto_detect_soc(Path(dts_files[0]).name)

    models = {}
    for f in dts_files:
        try:
            models[Path(f).name] = parse_dts_file(f, soc_name)
        except Exception as exc:
            click.echo(f"Error loading {f}: {exc}", err=True)
            raise SystemExit(1)

    from socc.sku_audit import compare_sku_models, render_sku_table, render_sku_json

    diff = compare_sku_models(models)

    if output_format == "json":
        text = render_sku_json(diff)
    else:
        text = render_sku_table(diff, use_color=color and output is None)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"SKU comparison report written to: {output}")
    else:
        click.echo(text)

    if diff.conflict_count > 0:
        raise SystemExit(1)
