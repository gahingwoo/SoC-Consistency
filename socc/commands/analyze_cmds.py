"""socc analyze <subcommand> — static analysis commands."""

from __future__ import annotations

import click

from socc.commands._shared import (
    parse_dts_file, Path, Optional,
)


@click.group("analyze")
def analyze_group():
    """Static analysis: memory map, dependency graph, bounds, IRQ, GC."""


# ── analyze memory ────────────────────────────────────────────────────────────

@analyze_group.command("memory")
@click.argument("dts_file")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def analyze_memory(dts_file: str, color: bool, output: Optional[str]):
    """Scan MMIO address map for overlapping or invalid regions.

    \b
    Error classes: MM-001 through MM-005 (duplicate, containment,
    partial overlap, zero-size, suspiciously large region).
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


# ── analyze deps ──────────────────────────────────────────────────────────────

@analyze_group.command("deps")
@click.argument("dts_file")
@click.option("--fan-out-limit", default=16, show_default=True, type=int,
              help="Warn when a single rail/clock drives more than N consumers")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def analyze_deps(dts_file: str, fan_out_limit: int,
                 color: bool, output: Optional[str]):
    """Detect dependency cycles and orphan nodes in the power/clock graph.

    \b
    Checks: DFS cycle detection, orphan references, fan-out anomaly.
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


# ── analyze bounds ────────────────────────────────────────────────────────────

@analyze_group.command("bounds")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default="rk3588", show_default=True,
              help="Target SoC name (used to look up hardware limits).")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]),
              show_default=True)
@click.option("-o", "--output", default=None)
def analyze_bounds(dts_file: str, soc: str, fmt: str, output: str):
    """Detect copy-paste hardware bounds violations.

    Checks GPIO pin indices, DMA channel numbers, and PWM channel
    indices against the physical limits of the target SoC.
    """
    from socc.bounds import check_bounds, render_bounds_text, render_bounds_json
    model = parse_dts_file(dts_file, soc)
    report = check_bounds(model)
    text = render_bounds_json(report) if fmt == "json" else render_bounds_text(report, use_color=True)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Bounds report written to {output}")
    else:
        click.echo(text)
    if not report.pass_result:
        raise SystemExit(1)


# ── analyze irq ───────────────────────────────────────────────────────────────

@analyze_group.command("irq")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default=None, help="SoC name (informational).")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]),
              show_default=True)
@click.option("-o", "--output", default=None)
def analyze_irq(dts_file: str, soc: str, fmt: str, output: str):
    """Detect IRQ collisions, reserved-PPI use, and routing mismatches.

    \b
    Checks: IRQ-C01 (collision), IRQ-C02 (reserved PPI),
            IRQ-C03 (missing controller), IRQ-C04 (missing cells).
    """
    from socc.irqcheck import check_irq, render_irq_text, render_irq_json
    model = parse_dts_file(dts_file, soc or "generic")
    report = check_irq(model)
    text = render_irq_json(report) if fmt == "json" else render_irq_text(report, use_color=True)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] IRQ report written to {output}")
    else:
        click.echo(text)
    if not report.pass_result:
        raise SystemExit(1)


# ── analyze gc ────────────────────────────────────────────────────────────────

@analyze_group.command("gc")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default=None, help="SoC name (e.g. rk3588)")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "json"]),
              show_default=True)
@click.option("--threshold", default=1, show_default=True,
              help="Minimum zombie count before non-zero exit.")
@click.option("-o", "--output", default=None)
def analyze_gc(dts_file: str, soc: str, fmt: str,
               threshold: int, output: str):
    """Find and report unreferenced (zombie) DTS nodes.

    Nodes that are disabled AND not transitively referenced by any
    enabled node.  Safe to delete to reduce DTB size and boot time.
    """
    from socc.gc import run_gc, render_gc_text, render_gc_json
    model = parse_dts_file(dts_file, soc or "generic")
    report = run_gc(model)
    text = render_gc_json(report) if fmt == "json" else render_gc_text(report, use_color=True)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] GC report written to {output}")
    else:
        click.echo(text)
    if report.zombie_count >= threshold:
        raise SystemExit(1)
