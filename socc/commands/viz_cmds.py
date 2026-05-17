"""socc viz <subcommand> — visualization commands."""

from __future__ import annotations

import click

from socc.commands._shared import (
    ALL_SOC_CHOICES, build_registry, auto_detect_soc,
    build_sample_model, parse_dts_file, Checker, Path, Optional,
)


@click.group("viz")
def viz_group():
    """Visualization: topology graph, BGA pinmap, power-rail sequence."""


# ── viz topology ──────────────────────────────────────────────────────────────

@viz_group.command("topology")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None)
@click.option("-o", "--output", default=None, metavar="FILE")
@click.option("--demo", is_flag=True, help="Use built-in sample model.")
@click.option("--no-open", is_flag=True,
              help="Do not open the HTML in the browser after generation.")
@click.option("--with-violations", is_flag=True,
              help="Run checker and annotate violations onto the graph.")
def viz_topology(dts_file: Optional[str], soc: Optional[str], output: Optional[str],
                 demo: bool, no_open: bool, with_violations: bool):
    """Generate an interactive HTML hardware topology graph.

    \b
    Example:
        socc viz topology board.dts --with-violations
    """
    import subprocess, sys
    from socc.visualize.topology import render_topology_html

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

    violations = []
    if with_violations:
        checker = Checker(build_registry())
        violations = checker.check(model, soc_name)

    out_path = output or f"{soc_name}-topology.html"
    title = f"SoC Topology — {soc_name}"
    if dts_file:
        title += f" ({Path(dts_file).name})"

    try:
        result = render_topology_html(model=model, output_path=out_path,
                                      violations=violations, title=title)
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
            pass


# ── viz pinmap ────────────────────────────────────────────────────────────────

@viz_group.command("pinmap")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", "soc_name", type=click.Choice(ALL_SOC_CHOICES),
              default=None, required=True,
              help="SoC identifier (must have a 'bga:' section in its YAML).")
@click.option("-o", "--output", default=None, metavar="FILE")
@click.option("--no-dts", is_flag=True,
              help="Generate heatmap from YAML data only, without DTS cross-reference.")
def viz_pinmap(dts_file: Optional[str], soc_name: str, output: Optional[str], no_dts: bool):
    """Generate an HTML BGA pinout heatmap for a SoC.

    \b
    Example:
        socc viz pinmap board.dts --soc rk3588
    """
    from socc.visualize.pinmap import render_bga_heatmap
    dts_pinmux = None
    if dts_file and not no_dts:
        try:
            model = parse_dts_file(dts_file, soc_name)
            dts_pinmux = model.pinmux_config or None
        except Exception as e:
            click.echo(f"Warning: could not parse DTS for cross-reference: {e}", err=True)

    out_path = output or f"{soc_name}-pinmap.html"
    try:
        result = render_bga_heatmap(soc_name=soc_name, output_path=out_path,
                                    dts_pinmux=dts_pinmux)
        click.echo(f"Pinout heatmap written to: {result}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


# ── viz power-seq ─────────────────────────────────────────────────────────────

@viz_group.command("power-seq")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=click.Choice(ALL_SOC_CHOICES), default=None)
@click.option("--color/--no-color", default=None)
@click.option("--demo", is_flag=True, help="Use built-in sample model.")
def viz_power_seq(dts_file: Optional[str], soc: Optional[str],
                  color: Optional[bool], demo: bool):
    """Visualise the power-rail startup sequence as an ASCII timing waveform.

    \b
    Example:
        socc viz power-seq board.dts
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
        soc_name = soc if (soc and soc != "auto") else auto_detect_soc(Path(dts_file).name)
        try:
            model = parse_dts_file(dts_file, soc_name)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)
        title = f"Power Rail Startup Sequence — {Path(dts_file).name}"
    click.echo(render_power_sequence(model, use_color=use_color, title=title))
