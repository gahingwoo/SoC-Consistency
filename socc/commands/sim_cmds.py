"""socc sim <subcommand> — simulation and live board commands."""

from __future__ import annotations

import click

from socc.commands._shared import (
    ALL_SOC_CHOICES, build_registry, auto_detect_soc,
    build_sample_model, parse_dts_file, Path, Optional,
)


@click.group("sim")
def sim_group():
    """Simulation, live-board check, trace, and migration commands."""


# ── sim failure ───────────────────────────────────────────────────────────────

@sim_group.command("failure")
@click.argument("node_name")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None)
@click.option("--color/--no-color", default=None)
def sim_failure(node_name: str, dts_file: str, soc: Optional[str], color: Optional[bool]):
    """Simulate a hardware failure and compute FMEA blast radius.

    \b
    Examples:
        socc sim failure vcc_3v3_sys board.dts
        socc sim failure /soc/i2c@fe2b0000/pmic@1b board.dts
    """
    from socc.fmea import simulate_failure, render_fmea_report
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    report = simulate_failure(model, node_name)
    click.echo(render_fmea_report(report, use_color=color))
    if report.severity in ("FATAL", "CRITICAL"):
        raise SystemExit(1)


# ── sim smoke ─────────────────────────────────────────────────────────────────

@sim_group.command("smoke")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None)
@click.option("--color/--no-color", default=None)
@click.option("-o", "--output", default=None, metavar="FILE")
def sim_smoke(dts_file: str, soc: Optional[str], color: Optional[bool], output: Optional[str]):
    """Simulate physical hardware damage from DTS configuration errors.

    Analyses voltage-domain mismatches, clock overspeeds, and missing
    power-sequencing that could destroy real hardware.

    \b
    Examples:
        socc sim smoke board.dts
        socc sim smoke board.dts --no-color -o casualty_report.txt
    """
    from socc.smoke import simulate_smoke as _sim_smoke, render_smoke_report
    use_color = color if color is not None else True
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
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


# ── sim shell ─────────────────────────────────────────────────────────────────

@sim_group.command("shell")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None)
@click.option("--demo", is_flag=True, help="Use built-in sample model.")
def sim_shell(dts_file: Optional[str], soc: Optional[str], demo: bool):
    """Interactive power/clock state machine simulator.

    \b
    Shell commands: status, tree, turn_off <rail>, turn_on <rail>,
                    check, help, quit
    """
    from socc.simulator.shell import PowerSimulator
    if demo:
        model = build_sample_model("rk3588")
        soc_name = "rk3588"
    else:
        if not dts_file:
            click.echo("Error: specify a DTS file or use --demo", err=True)
            raise SystemExit(1)
        soc_name = soc if (soc and soc != "auto") else auto_detect_soc(Path(dts_file).name)
        try:
            model = parse_dts_file(dts_file, soc_name)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)
    PowerSimulator(model, soc_name).run()


# ── sim live-check ────────────────────────────────────────────────────────────

@sim_group.command("live-check")
@click.argument("target")
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default="auto", show_default=True)
@click.option("--format", "output_format", type=click.Choice(["text", "json", "sarif"]),
              default="text", show_default=True)
@click.option("--min-severity", type=click.Choice(["error", "warning", "info"]),
              default="warning", show_default=True)
@click.option("--save-dts", default=None, metavar="FILE")
@click.option("--color/--no-color", default=None)
def sim_live_check(target: str, soc: str, output_format: str,
                   min_severity: str, save_dts: Optional[str], color: Optional[bool]):
    """Connect to a live board via SSH and run consistency checks.

    TARGET is user@host or user@host:port.

    \b
    Example:
        socc sim live-check root@192.168.1.50 --min-severity warning
    """
    from socc.live.connector import extract_live_dts
    from socc.commands._shared import filter_by_severity, Checker
    use_color = color
    click.echo(f"Connecting to {target} ...")
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
    checker = Checker(build_registry())
    violations = checker.check(model, soc_name)
    violations = filter_by_severity(violations, min_severity)
    report = checker.generate_report(violations, output_format, color=use_color)
    click.echo(report)
    if any(v.severity == "error" for v in violations):
        raise SystemExit(1)


# ── sim live-probe ────────────────────────────────────────────────────────────

@sim_group.command("live-probe")
@click.argument("dts_file")
@click.option("--svd", required=True, help="CMSIS-SVD XML file for the SoC")
@click.option("--simulate/--no-simulate", default=True, show_default=True,
              help="Use boot-default register values instead of live JTAG")
@click.option("--jtag-host", default="127.0.0.1", show_default=True)
@click.option("--jtag-port", default=4444, show_default=True, type=int)
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def sim_live_probe(dts_file: str, svd: str, simulate: bool, jtag_host: str,
                   jtag_port: int, color: bool, output: Optional[str]):
    """Silicon Lie Detector: compare DTS expectations vs physical register state.

    In --simulate mode (default) uses SVD reset values — no hardware needed.
    Without --simulate connects to a running OpenOCD instance.
    """
    from socc.live_probe import run_live_probe, render_probe_report
    soc = parse_dts_file(dts_file)
    report = run_live_probe(soc, svd_path=svd, dts_path=dts_file, simulate=simulate,
                            openocd_host=jtag_host, openocd_port=jtag_port)
    text = render_probe_report(report, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Report written to {output}")
    else:
        click.echo(text)
    if report.mismatch_count > 0:
        raise SystemExit(1)


# ── sim trace ─────────────────────────────────────────────────────────────────

@sim_group.command("trace")
@click.argument("node_path")
@click.argument("base_dts", type=click.Path(exists=True))
@click.argument("overlays", type=click.Path(exists=True), nargs=-1)
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None)
@click.option("--color/--no-color", default=None)
def sim_trace(node_path: str, base_dts: str, overlays: tuple,
              soc: Optional[str], color: Optional[bool]):
    """Trace how a DTS node's properties change across overlay layers.

    \b
    Examples:
        socc sim trace /soc/i2c@fe2b0000 base.dts
        socc sim trace /soc/i2c@fe2b0000 base.dts board.dts camera.dtbo
    """
    from socc.trace import trace_node, render_trace_report
    soc_name = soc or auto_detect_soc(Path(base_dts).name)
    report = trace_node(node_path=node_path, base_dts=base_dts,
                        overlays=list(overlays), soc_name=soc_name)
    click.echo(render_trace_report(report, use_color=color))


# ── sim migrate ───────────────────────────────────────────────────────────────

@sim_group.command("migrate")
@click.option("--from", "from_dts", type=click.Path(exists=True), required=True,
              metavar="OLD_DTS", help="Source board DTS (old SoC).")
@click.option("--to", "to_dts", type=click.Path(exists=True), default=None,
              metavar="NEW_BASE_DTS", help="Target SoC base DTS (optional).")
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None,
              help="New target SoC name.")
@click.option("-o", "--output", default=None, metavar="FILE")
@click.option("--color/--no-color", default=None)
def sim_migrate(from_dts: str, to_dts: Optional[str], soc: Optional[str],
                output: Optional[str], color: Optional[bool]):
    """Assist in migrating a board DTS to a new target SoC.

    \b
    Examples:
        socc sim migrate --from rk3399_board.dts --to rk3588.dtsi
        socc sim migrate --from old_board.dts --soc rk3588
    """
    from socc.migrate import migrate_dts, render_migration_report
    old_soc_name = auto_detect_soc(Path(from_dts).name)
    new_soc_name = soc or (auto_detect_soc(Path(to_dts).name) if to_dts else "unknown")
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
